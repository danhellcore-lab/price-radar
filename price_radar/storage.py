from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .config import DB_PATH
from .db import Database


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _schema(pk: str, real: str) -> list[str]:
    """DDL válido en SQLite y PostgreSQL salvo los tipos que difieren."""
    return [
        f"""
        CREATE TABLE IF NOT EXISTS products (
            id              {pk},
            url             TEXT NOT NULL UNIQUE,
            name            TEXT NOT NULL,
            currency        TEXT NOT NULL DEFAULT 'CLP',
            price_selector  TEXT,
            attr            TEXT,
            enabled         INTEGER NOT NULL DEFAULT 1,
            store           TEXT NOT NULL DEFAULT '',
            category        TEXT NOT NULL DEFAULT '',
            sku             TEXT NOT NULL DEFAULT '',
            created_at      TEXT NOT NULL
        )""",
        f"""
        CREATE TABLE IF NOT EXISTS categories (
            id          {pk},
            query       TEXT NOT NULL UNIQUE,
            enabled     INTEGER NOT NULL DEFAULT 1,
            created_at  TEXT NOT NULL
        )""",
        f"""
        CREATE TABLE IF NOT EXISTS observations (
            id          {pk},
            product_id  INTEGER NOT NULL,
            price       {real},
            ref_price   {real},
            ok          INTEGER NOT NULL DEFAULT 1,
            error       TEXT,
            ts          TEXT NOT NULL
        )""",
        f"""
        CREATE TABLE IF NOT EXISTS alerts (
            id          {pk},
            product_id  INTEGER NOT NULL,
            price       {real} NOT NULL,
            baseline    {real} NOT NULL,
            drop_pct    {real} NOT NULL,
            mad_score   {real} NOT NULL,
            reason      TEXT NOT NULL,
            notified    INTEGER NOT NULL DEFAULT 0,
            ts          TEXT NOT NULL
        )""",
        "CREATE INDEX IF NOT EXISTS idx_obs_product_ts ON observations(product_id, ts)",
        "CREATE INDEX IF NOT EXISTS idx_alerts_product_ts ON alerts(product_id, ts)",
        "CREATE INDEX IF NOT EXISTS idx_products_category ON products(category)",
    ]


class Storage:
    def __init__(self, path: Path | str = DB_PATH, url: str | None = None):
        self.db = Database(sqlite_path=Path(path), url=url)
        self._init_schema()

    @property
    def location(self) -> str:
        return self.db.describe()

    def _init_schema(self) -> None:
        self.db.execute_many_statements(_schema(self.db.pk, self.db.real))
        self._migrate()

    def _migrate(self) -> None:
        """Añade columnas que no existían en versiones anteriores.

        Se consulta el catálogo del motor en vez de intentar el ALTER a ciegas
        porque PostgreSQL aborta la transacción entera si el ALTER falla.
        """
        wanted = {
            "products": {
                "price_selector": "TEXT",
                "attr": "TEXT",
                "enabled": "INTEGER NOT NULL DEFAULT 1",
                "store": "TEXT NOT NULL DEFAULT ''",
                "category": "TEXT NOT NULL DEFAULT ''",
                "sku": "TEXT NOT NULL DEFAULT ''",
            },
            "observations": {"ref_price": self.db.real},
        }
        for table, columns in wanted.items():
            existing = self._columns(table)
            if not existing:
                continue
            for column, ddl in columns.items():
                if column not in existing:
                    self.db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")

    def _columns(self, table: str) -> set[str]:
        if self.db.postgres:
            rows = self.db.fetch_all(
                "SELECT column_name AS name FROM information_schema.columns "
                "WHERE table_name = ?",
                (table,),
            )
        else:
            rows = self.db.fetch_all(f"PRAGMA table_info({table})")
        return {r["name"] for r in rows}

    # ---------- productos ----------

    def upsert_product(
        self,
        url: str,
        name: str,
        currency: str = "CLP",
        price_selector: str | None = None,
        attr: str | None = None,
        enabled: bool = True,
    ) -> int:
        self.db.execute(
            "INSERT INTO products (url, name, currency, price_selector, attr, enabled, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(url) DO UPDATE SET name=excluded.name, currency=excluded.currency, "
            "price_selector=excluded.price_selector, attr=excluded.attr, enabled=excluded.enabled",
            (url, name, currency, price_selector, attr, 1 if enabled else 0, utcnow()),
        )
        row = self.db.fetch_one("SELECT id FROM products WHERE url = ?", (url,))
        return int(row["id"])

    def upsert_discovered(
        self, url: str, name: str, store: str, category: str, sku: str, currency: str = "CLP"
    ) -> int:
        """Alta de un producto encontrado por descubrimiento automático.

        No pisa `name` si ya existe: el nombre del listado puede variar entre
        búsquedas y no queremos que el histórico parezca de otro producto.
        """
        self.db.execute(
            "INSERT INTO products (url, name, currency, store, category, sku, enabled, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 1, ?) "
            "ON CONFLICT(url) DO UPDATE SET store=excluded.store, category=excluded.category, "
            "sku=excluded.sku",
            (url, name, currency, store, category, sku, utcnow()),
        )
        row = self.db.fetch_one("SELECT id FROM products WHERE url = ?", (url,))
        return int(row["id"])

    def upsert_discovered_bulk(self, items: list[dict[str, Any]]) -> dict[str, int]:
        """Da de alta muchos productos de golpe y devuelve {url: id}.

        Una búsqueda trae ~200 productos. Uno por uno serían 200 idas y vueltas
        a la base; contra Postgres remoto eso es la diferencia entre segundos y
        varios minutos por ciclo.
        """
        if not items:
            return {}

        now = utcnow()
        rows = [
            (i["url"], i["name"], i.get("currency", "CLP"), i.get("store", ""),
             i.get("category", ""), i.get("sku", ""), now)
            for i in items
        ]
        sql = self.db.q(
            "INSERT INTO products (url, name, currency, store, category, sku, enabled, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 1, ?) "
            "ON CONFLICT(url) DO UPDATE SET store=excluded.store, category=excluded.category, "
            "sku=excluded.sku"
        )
        with self.db.connect() as conn:
            cur = conn.cursor()
            cur.executemany(sql, rows)
            if self.db.postgres:
                conn.commit()

        urls = [i["url"] for i in items]
        return {r["url"]: int(r["id"]) for r in self._products_by_url(urls)}

    def _products_by_url(self, urls: list[str]) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        # Se trocea para no chocar con el límite de parámetros de la consulta.
        for start in range(0, len(urls), 400):
            chunk = urls[start:start + 400]
            marks = ",".join("?" for _ in chunk)
            found += self.db.fetch_all(
                f"SELECT id, url FROM products WHERE url IN ({marks})", chunk
            )
        return found

    def record_observations_bulk(self, rows: list[tuple[int, float | None, float | None]]) -> None:
        """rows = [(product_id, price, ref_price), ...]"""
        if not rows:
            return
        now = utcnow()
        sql = self.db.q(
            "INSERT INTO observations (product_id, price, ref_price, ok, error, ts) "
            "VALUES (?, ?, ?, 1, NULL, ?)"
        )
        with self.db.connect() as conn:
            cur = conn.cursor()
            cur.executemany(sql, [(pid, price, ref, now) for pid, price, ref in rows])
            if self.db.postgres:
                conn.commit()

    def histories_by_category(self, category: str, days: int) -> dict[int, list[float]]:
        """Precios históricos de todos los productos de una categoría, de una vez."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
        rows = self.db.fetch_all(
            "SELECT o.product_id, o.price FROM observations o "
            "JOIN products p ON p.id = o.product_id "
            "WHERE p.category = ? AND o.ok = 1 AND o.price IS NOT NULL AND o.ts >= ? "
            "ORDER BY o.ts ASC",
            (category, cutoff),
        )
        out: dict[int, list[float]] = {}
        for r in rows:
            out.setdefault(int(r["product_id"]), []).append(float(r["price"]))
        return out

    def last_alert_times(self) -> dict[int, str]:
        """Momento de la última alerta de cada producto, para el cooldown."""
        rows = self.db.fetch_all(
            "SELECT product_id, MAX(ts) AS ts FROM alerts GROUP BY product_id"
        )
        return {int(r["product_id"]): r["ts"] for r in rows}

    def products(self) -> list[dict[str, Any]]:
        return self.db.fetch_all("SELECT * FROM products ORDER BY name")

    def product(self, product_id: int) -> dict[str, Any] | None:
        return self.db.fetch_one("SELECT * FROM products WHERE id = ?", (product_id,))

    def set_enabled(self, product_id: int, enabled: bool) -> None:
        self.db.execute(
            "UPDATE products SET enabled = ? WHERE id = ?", (1 if enabled else 0, product_id)
        )

    def delete_product(self, product_id: int) -> None:
        self.db.execute("DELETE FROM observations WHERE product_id = ?", (product_id,))
        self.db.execute("DELETE FROM alerts WHERE product_id = ?", (product_id,))
        self.db.execute("DELETE FROM products WHERE id = ?", (product_id,))

    # ---------- categorías ----------

    def add_category(self, query: str) -> int:
        clean = query.strip().lower()
        self.db.execute(
            "INSERT INTO categories (query, enabled, created_at) VALUES (?, 1, ?) "
            "ON CONFLICT(query) DO UPDATE SET enabled = 1",
            (clean, utcnow()),
        )
        row = self.db.fetch_one("SELECT id FROM categories WHERE query = ?", (clean,))
        return int(row["id"])

    def categories(self) -> list[dict[str, Any]]:
        return self.db.fetch_all("SELECT * FROM categories ORDER BY query")

    def delete_category(self, category_id: int) -> None:
        """Borra la categoría y todos los productos que descubrió."""
        row = self.db.fetch_one("SELECT query FROM categories WHERE id = ?", (category_id,))
        if row:
            for p in self.db.fetch_all(
                "SELECT id FROM products WHERE category = ?", (row["query"],)
            ):
                self.delete_product(int(p["id"]))
        self.db.execute("DELETE FROM categories WHERE id = ?", (category_id,))

    def count_products_in_category(self, query: str) -> int:
        row = self.db.fetch_one(
            "SELECT COUNT(*) AS n FROM products WHERE category = ?", (query,)
        )
        return int(row["n"]) if row else 0

    # ---------- observaciones ----------

    def record_observation(
        self,
        product_id: int,
        price: float | None,
        ok: bool,
        error: str | None = None,
        ref_price: float | None = None,
    ) -> None:
        self.db.execute(
            "INSERT INTO observations (product_id, price, ref_price, ok, error, ts) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (product_id, price, ref_price, 1 if ok else 0, error, utcnow()),
        )

    def history(self, product_id: int, days: int | None = None) -> list[dict[str, Any]]:
        """Observaciones exitosas, más antiguas primero."""
        sql = ("SELECT price, ts FROM observations "
               "WHERE product_id = ? AND ok = 1 AND price IS NOT NULL")
        params: list[Any] = [product_id]
        if days is not None:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
            sql += " AND ts >= ?"
            params.append(cutoff)
        return self.db.fetch_all(sql + " ORDER BY ts ASC", params)

    def last_observation(self, product_id: int) -> dict[str, Any] | None:
        return self.db.fetch_one(
            "SELECT * FROM observations WHERE product_id = ? ORDER BY ts DESC LIMIT 1",
            (product_id,),
        )

    def latest_observations(self) -> dict[int, dict[str, Any]]:
        """Última lectura de cada producto, en una sola consulta.

        Pedirlas una por una eran cientos de viajes a la base; contra Postgres
        remoto eso hacía que la pantalla tardara minutos en dibujarse.
        """
        rows = self.db.fetch_all(
            "SELECT o.* FROM observations o "
            "JOIN (SELECT product_id, MAX(ts) AS ts FROM observations GROUP BY product_id) m "
            "  ON m.product_id = o.product_id AND m.ts = o.ts"
        )
        return {int(r["product_id"]): r for r in rows}

    def price_stats(self, days: int = 30) -> dict[int, dict[str, Any]]:
        """Nº de lecturas, mínimo y máximo por producto, de una sola vez."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
        rows = self.db.fetch_all(
            "SELECT product_id, COUNT(*) AS n, MIN(price) AS lo, MAX(price) AS hi "
            "FROM observations WHERE ok = 1 AND price IS NOT NULL AND ts >= ? "
            "GROUP BY product_id",
            (cutoff,),
        )
        return {int(r["product_id"]): r for r in rows}

    # ---------- alertas ----------

    def last_alert(self, product_id: int) -> dict[str, Any] | None:
        return self.db.fetch_one(
            "SELECT * FROM alerts WHERE product_id = ? ORDER BY ts DESC LIMIT 1", (product_id,)
        )

    def record_alert(
        self,
        product_id: int,
        price: float,
        baseline: float,
        drop_pct: float,
        mad_score: float,
        reason: str,
        notified: bool,
    ) -> None:
        self.db.execute(
            "INSERT INTO alerts (product_id, price, baseline, drop_pct, mad_score, reason, "
            "notified, ts) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (product_id, price, baseline, drop_pct, mad_score, reason,
             1 if notified else 0, utcnow()),
        )

    def recent_alerts(self, limit: int = 50) -> list[dict[str, Any]]:
        return self.db.fetch_all(
            "SELECT a.*, p.name, p.url, p.currency, p.store, p.category FROM alerts a "
            "JOIN products p ON p.id = a.product_id ORDER BY a.ts DESC LIMIT ?",
            (limit,),
        )
