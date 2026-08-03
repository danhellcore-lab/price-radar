"""Adaptadores de tiendas: convierten una búsqueda en una lista de productos.

Cada tienda expone su catálogo de forma distinta. Lo que tienen en común las
tiendas soportadas es que sirven los resultados como JSON dentro del HTML
(`__NEXT_DATA__`), lo que es mucho más estable que rascar selectores CSS: el
diseño de la página cambia seguido, la estructura de datos casi nunca.

Una sola petición a una búsqueda devuelve ~50 productos con su precio. Por eso
el descubrimiento por categorías es más barato para las tiendas que revisar
producto por producto.
"""
from __future__ import annotations

import json
import logging
import re
import time
import unicodedata
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

from bs4 import BeautifulSoup

log = logging.getLogger(__name__)


@dataclass
class Found:
    """Un producto encontrado en un listado."""

    store: str
    name: str
    url: str
    price: float
    reference_price: float | None = None  # precio tachado / normal, si la tienda lo da
    brand: str = ""
    sku: str = ""

    @property
    def discount_pct(self) -> float | None:
        """Descuento declarado por la propia tienda."""
        if not self.reference_price or self.reference_price <= 0:
            return None
        if self.price >= self.reference_price:
            return None
        return (self.reference_price - self.price) / self.reference_price * 100.0


def _to_number(raw: Any) -> float | None:
    """'$ 1.299.990' o '1.299.990' o 1299990 -> 1299990.0"""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw) if raw > 0 else None
    digits = re.sub(r"[^\d]", "", str(raw))
    if not digits:
        return None
    value = float(digits)
    return value if value > 0 else None


def _next_data(html: str) -> dict[str, Any] | None:
    tag = BeautifulSoup(html, "lxml").find("script", id="__NEXT_DATA__")
    if not tag or not tag.string:
        return None
    try:
        return json.loads(tag.string)
    except json.JSONDecodeError:
        return None


def _dig(node: Any, *path: str) -> Any:
    for key in path:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


class StoreAdapter(ABC):
    name: str
    label: str
    site: str = ""          # dominio, para mostrarlo en la interfaz
    how: str = ""           # por dónde entra: búsqueda, categorías…
    note: str = ""          # limitación conocida, si la hay

    @abstractmethod
    def listing_urls(self, query: str, scraper: Any) -> list[str]:
        """URLs de listado que hay que descargar para esta categoría.

        Recibe el scraper porque algunas tiendas exigen un paso previo (por
        ejemplo, resolver la categoría contra su índice de sitemap).
        """

    @abstractmethod
    def parse(self, html: str) -> list[Found]:
        ...


class FalabellaGroup(StoreAdapter):
    """Falabella y Sodimac comparten plataforma: mismo JSON, distinto dominio.

    Verificado: el mismo parser devuelve 56 productos en ambas. Se hereda en
    vez de duplicarse para que un cambio de la plataforma se arregle una vez.
    """

    host: str
    path: str

    def listing_urls(self, query: str, scraper: Any) -> list[str]:
        # El robots.txt de ambas permite /search.
        return [f"{self.host}{self.path}?Ntt={quote_plus(query)}"]

    def parse(self, html: str) -> list[Found]:
        data = _next_data(html)
        results = _dig(data, "props", "pageProps", "results") or []
        found: list[Found] = []

        for item in results:
            if not isinstance(item, dict):
                continue
            name = item.get("displayName")
            url = item.get("url")
            if not name or not url:
                continue

            # `prices` trae varias variantes: con tarjeta, de evento, y el
            # normal tachado. El precio real a pagar es el menor no tachado;
            # el tachado es la referencia para calcular el descuento.
            current: list[float] = []
            reference: list[float] = []
            for entry in item.get("prices") or []:
                values = entry.get("price") or []
                amount = _to_number(values[0] if values else None)
                if amount is None:
                    continue
                (reference if entry.get("crossed") else current).append(amount)

            if not current:
                continue

            found.append(
                Found(
                    store=self.name,
                    name=str(name).strip(),
                    url=str(url),
                    price=min(current),
                    reference_price=max(reference) if reference else None,
                    brand=str(item.get("brand") or ""),
                    sku=str(item.get("productId") or ""),
                )
            )
        return found


class Falabella(FalabellaGroup):
    name = "falabella"
    label = "Falabella"
    host = "https://www.falabella.com"
    path = "/falabella-cl/search"
    site = "falabella.com"
    how = "Buscador del sitio"


class Sodimac(FalabellaGroup):
    name = "sodimac"
    label = "Sodimac"
    host = "https://www.sodimac.cl"
    path = "/sodimac-cl/search"
    site = "sodimac.cl"
    how = "Buscador del sitio"
    note = "Solo ferretería, hogar y construcción"


# "$4.233 x 100 ml", "$7.990 x 100 un": precio por unidad de medida, no lo que
# se paga. Paris lo muestra junto al precio real y contarlo como un precio más
# rompía las dos puntas: en un envase grande es menor que el precio real (y se
# tomaba como el precio), y en uno pequeño es mucho mayor (y se tomaba como el
# precio anterior, inventando un descuentazo).
UNIT_PRICE_RE = re.compile(
    r"x\s*\d+(?:[.,]\d+)?\s*(ml|cc|l|lt|lts|g|gr|kg|un|und|unid|unidad|m|mt|cm|hoja|hojas|"
    r"rollo|rollos|pack|dosis|lavado|lavados|capsula|capsulas)\b",
    re.IGNORECASE,
)


class Paris(StoreAdapter):
    """Paris no incrusta un JSON de resultados, pero sí marca cada tarjeta con
    atributos `data-cnstrc-*` (Constructor.io) que traen nombre e id.

    El precio se lee del texto de la tarjeta: aparecen varios (con tarjeta, de
    internet, normal). El menor es lo que se paga; el mayor es la referencia.
    Los precios por unidad de medida se descartan antes de comparar.
    """

    name = "paris"
    label = "Paris"
    site = "paris.cl"
    how = "Buscador del sitio"
    note = "Bloquea a los servidores de GitHub: solo funciona desde tu PC"
    HOST = "https://www.paris.cl"

    def listing_urls(self, query: str, scraper: Any) -> list[str]:
        return [f"{self.HOST}/search?q={quote_plus(query)}"]

    @staticmethod
    def _real_prices(card: Any) -> list[float]:
        """Importes que se pagan de verdad, sin los precios por unidad."""
        montos: set[float] = set()
        for fragment in card.stripped_strings:
            if UNIT_PRICE_RE.search(fragment):
                continue
            for match in re.findall(r"\$\s?[\d.]+", fragment):
                value = _to_number(match)
                if value:
                    montos.add(value)
        return sorted(montos)

    def parse(self, html: str) -> list[Found]:
        soup = BeautifulSoup(html, "lxml")
        found: list[Found] = []

        for card in soup.select("[data-cnstrc-item-id]"):
            name = (card.get("data-cnstrc-item-name") or "").strip()
            link = card.find("a", href=True)
            if not name or not link:
                continue

            amounts = self._real_prices(card)
            if not amounts:
                continue

            href = link["href"]
            url = href if href.startswith("http") else f"{self.HOST}{href}"

            found.append(
                Found(
                    store=self.name,
                    name=name,
                    url=url,
                    price=amounts[0],
                    reference_price=amounts[-1] if len(amounts) > 1 else None,
                    sku=str(card.get("data-cnstrc-item-id") or ""),
                )
            )
        return found


class Ripley(StoreAdapter):
    """Ripley prohíbe /search/ en su robots.txt, así que no se usa la búsqueda.

    Sí permite las páginas de categoría, y publica el listado completo de
    categorías en su sitemap. Se resuelve la categoría del usuario contra ese
    índice, que se cachea en disco porque cambia muy poco.
    """

    name = "ripley"
    label = "Ripley"
    site = "simple.ripley.cl"
    how = "Páginas de categoría (su robots.txt prohíbe el buscador)"
    note = "Bloquea a los servidores de GitHub: solo funciona desde tu PC"

    SITEMAP_INDEX = "https://simple.ripley.cl/sitemap_ripley_categorias.xml"
    CACHE_DAYS = 7

    def __init__(self) -> None:
        self._index: list[str] | None = None

    # -- índice de categorías --

    def _cache_file(self) -> Path:
        from .config import data_dir

        return data_dir() / "ripley_categorias.json"

    def _load_index(self, scraper: Any) -> list[str]:
        if self._index is not None:
            return self._index

        cache = self._cache_file()
        if cache.exists():
            age = time.time() - cache.stat().st_mtime
            if age < self.CACHE_DAYS * 86400:
                try:
                    self._index = json.loads(cache.read_text(encoding="utf-8"))
                    return self._index
                except (json.JSONDecodeError, OSError):
                    pass

        log.info("Construyendo el índice de categorías de Ripley…")
        urls: set[str] = set()
        try:
            index_xml = scraper.get_html(self.SITEMAP_INDEX)
        except Exception as exc:
            log.warning("No pude leer el sitemap de Ripley: %s", exc)
            self._index = []
            return self._index

        departments = sorted(set(re.findall(r"<loc>(.*?)</loc>", index_xml)))
        for dept in departments:
            try:
                xml = scraper.get_html(dept, polite_delay=1.5)
            except Exception as exc:
                log.debug("sitemap %s falló: %s", dept, exc)
                continue
            urls.update(re.findall(r"<loc>(.*?)</loc>", xml))

        self._index = sorted(u for u in urls if u.count("/") >= 4 and self._is_real_category(u))
        try:
            cache.write_text(json.dumps(self._index), encoding="utf-8")
        except OSError:
            pass
        log.info("Índice de Ripley: %d categorías", len(self._index))
        return self._index

    # Rutas internas de la tienda (entornos de prueba, catálogos de
    # operaciones) que aparecen en el sitemap pero no son categorías reales.
    INTERNAL = ("-demo", "demo-", "rscore", "automated", "whitelist",
                "catalogacion-restringida", "operaciones", "products-mdco",
                "product_test", "/list")

    @classmethod
    def _is_real_category(cls, url: str) -> bool:
        lowered = unicodedata.normalize("NFKD", url.lower()).encode("ascii", "ignore").decode()
        return not any(marker in lowered for marker in cls.INTERNAL)

    @staticmethod
    def _words(text: str) -> list[str]:
        clean = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
        return [w for w in re.split(r"[^a-z0-9]+", clean.lower()) if len(w) > 2]

    @staticmethod
    def _matches(query_word: str, slug_word: str) -> bool:
        """Compara tolerando plurales y variantes de raíz.

        Las tiendas nombran sus categorías en plural o con otra forma de la
        misma palabra: notebook/notebooks, celular/celulares,
        televisor/television. Comparar palabra exacta pierde todos esos casos.
        """
        if query_word == slug_word:
            return True
        shortest = min(len(query_word), len(slug_word))
        if shortest < 5:
            return False
        prefix = 0
        for a, b in zip(query_word, slug_word):
            if a != b:
                break
            prefix += 1
        return prefix >= max(5, shortest - 3)

    def listing_urls(self, query: str, scraper: Any) -> list[str]:
        index = self._load_index(scraper)
        if not index:
            return []

        wanted = self._words(query)
        if not wanted:
            return []

        scored: list[tuple[float, int, str]] = []
        for url in index:
            path = url.split("simple.ripley.cl/", 1)[-1]
            segments = path.split("/")
            slug_words = self._words(path)
            last_words = set(self._words(segments[-1]))

            hits = sum(1 for q in wanted if any(self._matches(q, s) for s in slug_words))
            if not hits:
                continue

            score = hits / len(wanted)
            # Si la palabra buscada es el último tramo de la ruta, esa página ES
            # la categoría; si aparece antes, es un filtro dentro de otra cosa.
            if any(self._matches(q, s) for q in wanted for s in last_words):
                score += 0.5
            scored.append((score, len(segments), url))

        if not scored:
            return []
        # Mejor cobertura, luego ruta más corta, y la URL alfabética como
        # desempate: sin él el orden dependía del azar del texto.
        scored.sort(key=lambda t: (-t[0], t[1], t[2]))
        # Se devuelven varias candidatas y luego se fusionan los resultados:
        # es más fiable medir cuál trae productos que adivinarlo por el nombre.
        return [url for _, _, url in scored[:3]]

    def parse(self, html: str) -> list[Found]:
        data = _next_data(html)
        products = _dig(data, "props", "pageProps", "findabilityProps", "data", "products") or []
        found: list[Found] = []

        for item in products:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            sku = item.get("sku")
            price = _to_number(item.get("priceNumber") or item.get("price"))
            if not name or not sku or price is None:
                continue

            found.append(
                Found(
                    store=self.name,
                    name=str(name).strip(),
                    url=f"https://simple.ripley.cl/search/{sku}",  # redirige a la ficha
                    price=price,
                    reference_price=_to_number(item.get("oldPrice")),
                    brand=str(item.get("brand") or ""),
                    sku=str(sku),
                )
            )
        return found


ADAPTERS: list[StoreAdapter] = [Falabella(), Paris(), Ripley(), Sodimac()]
BY_NAME = {a.name: a for a in ADAPTERS}
