from __future__ import annotations

import logging
from typing import Any, Callable

from .alerts import TelegramNotifier, format_alert
from .config import Config, Target
from .detector import Detector
from .discovery import Discovery, ListingAlert
from .scraper import Scraper
from .storage import Storage

log = logging.getLogger(__name__)


class Engine:
    def __init__(self, config: Config, storage: Storage | None = None):
        self.config = config
        self.storage = storage or Storage()
        self.scraper = Scraper(config.scraper)
        self.detector = Detector(config.detector)
        self.notifier = TelegramNotifier(config.alerts)
        self.discovery = Discovery(self.scraper, config.discovery)
        self.seed_targets_from_config()

    def reload_settings(self) -> None:
        """Reconstruye los componentes tras cambiar ajustes desde la interfaz."""
        self.scraper = Scraper(self.config.scraper)
        self.detector = Detector(self.config.detector)
        self.notifier = TelegramNotifier(self.config.alerts)
        self.discovery = Discovery(self.scraper, self.config.discovery)

    # ---------- descubrimiento por categorías ----------

    def scan_category(
        self, query: str, progress: Callable[[str], None] | None = None
    ) -> dict[str, Any]:
        """Busca una categoría, guarda todo lo encontrado y detecta anomalías.

        Una búsqueda devuelve ~50 productos por tienda con su precio, así que
        con una petición por tienda se actualiza el histórico de todo el lote.
        """
        items, errors = self.discovery.search(query, progress=progress)
        if not items:
            return {"query": query, "found": 0, "alerts": 0, "errors": errors}

        listing_alerts = {a.found.url: a for a in self.discovery.detect(items)}
        raised = 0

        # El historial se lee ANTES de guardar las lecturas nuevas, para no
        # contaminar el baseline con el precio que estamos evaluando.
        histories = self.storage.histories_by_category(query, self.detector.window_days)
        cooldowns = self.storage.last_alert_times()

        ids = self.storage.upsert_discovered_bulk([
            {"url": i.url, "name": i.name, "store": i.store, "category": query, "sku": i.sku}
            for i in items
        ])
        self.storage.record_observations_bulk([
            (ids[i.url], i.price, i.reference_price) for i in items if i.url in ids
        ])

        for item in items:
            product_id = ids.get(item.url)
            if product_id is None:
                continue

            alert = listing_alerts.get(item.url)
            if alert is None:
                # Sin señal inmediata, queda la comparación con su propio pasado.
                past = [{"price": p} for p in histories.get(product_id, [])]
                verdict = self.detector.evaluate(item.price, past)
                if not verdict.is_anomaly:
                    continue
                reference, drop, reason = verdict.baseline, verdict.drop_pct, verdict.reason
                mad = verdict.mad_score if verdict.mad_score != float("inf") else 999.0
            else:
                reference, drop, reason = alert.reference, alert.drop_pct, alert.reason
                mad = 0.0

            previous = cooldowns.get(product_id)
            if previous and self.detector.in_cooldown({"ts": previous}):
                continue

            notified = self.notifier.send(
                format_alert(item.name, item.url, "CLP", item.price, reference, reason)
            )
            self.storage.record_alert(
                product_id, item.price, reference, drop, mad, reason, notified
            )
            raised += 1
            log.warning("ALERTA [%s] %s -- %s", query, item.name[:60], reason)

        return {"query": query, "found": len(items), "alerts": raised, "errors": errors}

    def scan_all_categories(
        self, progress: Callable[[str], None] | None = None
    ) -> dict[str, Any]:
        totals = {"categories": 0, "found": 0, "alerts": 0, "errors": []}
        for row in self.storage.categories():
            if not row["enabled"]:
                continue
            result = self.scan_category(row["query"], progress=progress)
            totals["categories"] += 1
            totals["found"] += result["found"]
            totals["alerts"] += result["alerts"]
            totals["errors"] += result["errors"]
        return totals

    def seed_targets_from_config(self) -> None:
        """Importa los targets de config.yaml la primera vez (base vacía)."""
        pending = self.config.active_targets
        if not pending or self.storage.products():
            return
        for t in pending:
            self.storage.upsert_product(
                t.url, t.name, t.currency, t.price_selector, t.attr, t.enabled
            )
        log.info("Importados %d producto(s) desde config.yaml", len(pending))

    def targets(self) -> list[tuple[int, Target]]:
        """Solo los productos añadidos a mano por URL.

        Los productos descubiertos por categoría NO se piden uno por uno: su
        precio ya viene en el listado de la tienda. Recorrerlos individualmente
        serían cientos de peticiones por ciclo para obtener el mismo dato.
        """
        return [
            (
                row["id"],
                Target(
                    name=row["name"],
                    url=row["url"],
                    price_selector=row["price_selector"],
                    attr=row["attr"],
                    currency=row["currency"],
                    enabled=bool(row["enabled"]),
                ),
            )
            for row in self.storage.products()
            if row["enabled"] and not row["store"]
        ]

    def run_cycle(self, progress: Callable[[int, int, str], None] | None = None) -> dict[str, int]:
        """Recorre todos los productos activos una vez.

        `progress` recibe (índice, total, nombre) antes de revisar cada producto,
        para que la interfaz pueda mostrar avance.
        """
        stats = {"checked": 0, "ok": 0, "failed": 0, "alerts": 0}
        targets = self.targets()
        log.info("Iniciando ciclo sobre %d producto(s)", len(targets))

        for index, (product_id, target) in enumerate(targets, start=1):
            if progress:
                progress(index, len(targets), target.name)
            stats["checked"] += 1
            result = self.scraper.fetch(target)

            if not result.ok:
                stats["failed"] += 1
                self.storage.record_observation(product_id, None, ok=False, error=result.error)
                log.warning("%s -> fallo: %s", target.name, result.error)
                continue

            stats["ok"] += 1
            price = result.price
            # El historial se lee ANTES de guardar, para no contaminar el baseline.
            history = self.storage.history(product_id, days=self.detector.window_days)
            self.storage.record_observation(product_id, price, ok=True)
            log.info("%s -> %s %.2f", target.name, target.currency, price)

            verdict = self.detector.evaluate(price, history)
            if not verdict.is_anomaly:
                continue
            if self.detector.in_cooldown(self.storage.last_alert(product_id)):
                log.info("%s: anomalía en cooldown, no se notifica", target.name)
                continue

            message = format_alert(
                target.name, target.url, target.currency, price, verdict.baseline, verdict.reason
            )
            notified = self.notifier.send(message)
            self.storage.record_alert(
                product_id, price, verdict.baseline, verdict.drop_pct,
                verdict.mad_score if verdict.mad_score != float("inf") else 999.0,
                verdict.reason, notified,
            )
            stats["alerts"] += 1
            log.warning("ALERTA: %s -- %s", target.name, verdict.reason)

        log.info("Ciclo terminado: %s", stats)
        return stats
