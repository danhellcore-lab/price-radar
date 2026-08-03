"""Descubrimiento por categorías y detección sobre listados.

El usuario da una categoría ("notebook", "zapatillas"); esto la busca en cada
tienda soportada y devuelve todos los productos con su precio.

Sobre ese lote se aplican dos detecciones que NO necesitan historial, y por eso
avisan desde el primer minuto:

1. **Descuento declarado extremo** — la propia tienda publica el precio normal
   tachado. Medido sobre 609 descuentos reales de 6 categorías, la mediana del
   retail es 35% y el máximo observado 74,9%. Un descuento por encima de ~80%
   no es una promoción, es un error.

2. **Comparación entre tiendas** — el mismo producto listado mucho más barato
   en una tienda que en las otras.

La tercera señal (precio contra su propio histórico) vive en `detector.py` y
entra en juego cuando el producto lleva varias revisiones.
"""
from __future__ import annotations

import logging
import re
import statistics
import time
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Callable, Iterable

from .scraper import Scraper
from .stores import ADAPTERS, Found, StoreAdapter

log = logging.getLogger(__name__)

# Palabras que no ayudan a decidir si dos anuncios son el mismo producto.
STOPWORDS = {
    "de", "la", "el", "para", "con", "sin", "y", "a", "en", "por", "un", "una",
    "los", "las", "del", "al", "cm", "mm", "pulgadas", "color", "talla",
    "hombre", "mujer", "unisex", "niño", "nina", "niña",
}


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9 ]+", " ", text.lower())


def tokens(name: str) -> set[str]:
    """Palabras significativas de un título de producto.

    Los códigos de modelo (15-fb3026la, rtx3050) son los que de verdad
    identifican un producto, así que se conservan enteros.
    """
    return {
        w for w in normalize(name).split()
        if len(w) > 2 and w not in STOPWORDS
    }


# "16gb", "512gb", "15w", "50ml": son especificaciones, no identifican el
# modelo. Casi todos los productos de una categoría comparten varias, así que
# tomarlas por código de modelo hacía que todo pareciera el mismo producto.
UNIT_RE = re.compile(
    r"^\d+(gb|tb|mb|kb|ghz|mhz|hz|kw|w|v|mah|ml|lt|l|cm|mm|kg|gr|g|pulgadas|pulg|hs|h)$"
)


def model_codes(name: str) -> set[str]:
    """Códigos de modelo: mezclan letras y números (fb3026la, 12450hx, rtx5070).

    Son lo único que distingue de verdad dos variantes del mismo producto. Sin
    ellos, "Victus i5 16GB" y "Victus i7 32GB" parecen el mismo artículo.
    """
    out = set()
    for word in normalize(name).split():
        if len(word) < 5 or UNIT_RE.match(word):
            continue
        if any(c.isdigit() for c in word) and any(c.isalpha() for c in word):
            out.add(word)
    return out


def similarity(a: set[str], b: set[str]) -> float:
    """Jaccard: proporción de palabras compartidas."""
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def same_product(name_a: str, name_b: str, tokens_a: set[str], tokens_b: set[str],
                 threshold: float) -> bool:
    """¿Son el mismo producto, no solo productos parecidos?

    Si ambos anuncios declaran códigos de modelo, tienen que compartir al menos
    uno: es la única forma de no comparar un i5 contra un i7 y cantar un
    "error de precio" que en realidad es otra máquina.
    """
    codes_a, codes_b = model_codes(name_a), model_codes(name_b)
    if codes_a and codes_b and not (codes_a & codes_b):
        return False
    return similarity(tokens_a, tokens_b) >= threshold


@dataclass
class ListingAlert:
    found: Found
    kind: str          # "descuento" | "entre-tiendas"
    reference: float   # precio con el que se comparó
    drop_pct: float
    reason: str


class Discovery:
    def __init__(self, scraper: Scraper, settings: dict[str, Any] | None = None):
        self.scraper = scraper
        s = settings or {}
        # 80%: por encima del máximo observado en retail normal (74,9%).
        self.min_discount_pct = s.get("min_discount_pct", 80.0)
        # Para comparar entre tiendas basta con menos: ahí la referencia es el
        # precio real de la competencia, no un "precio normal" inflado.
        self.min_cross_store_pct = s.get("min_cross_store_pct", 45.0)
        self.similarity_threshold = s.get("similarity_threshold", 0.6)
        self.max_per_store = s.get("max_per_store", 60)
        self.min_price = s.get("min_price", 1000.0)

    # ---------- búsqueda ----------

    def search(
        self,
        query: str,
        adapters: Iterable[StoreAdapter] | None = None,
        progress: Callable[[str], None] | None = None,
    ) -> tuple[list[Found], list[str]]:
        """Busca la categoría en cada tienda. Devuelve (productos, errores)."""
        results: list[Found] = []
        errors: list[str] = []

        for adapter in adapters or ADAPTERS:
            if progress:
                progress(f"Buscando «{query}» en {adapter.label}…")

            try:
                urls = adapter.listing_urls(query, self.scraper)
            except Exception as exc:
                errors.append(f"{adapter.label}: {exc}")
                log.warning("%s no pudo resolver «%s»: %s", adapter.label, query, exc)
                continue

            if not urls:
                # Que una tienda no cubra una categoría es lo normal (Sodimac no
                # vende zapatillas). No es un error que merezca avisar.
                log.info("%s no cubre «%s»", adapter.label, query)
                continue

            items: list[Found] = []
            for url in urls:
                try:
                    items.extend(adapter.parse(self.scraper.get_html(url)))
                except Exception as exc:
                    errors.append(f"{adapter.label}: {exc}")
                    log.warning("%s falló en %s: %s", adapter.label, url, exc)

            if not items:
                log.info("%s: sin resultados para «%s»", adapter.label, query)
                continue

            # Un mismo producto puede salir en dos listados de la tienda.
            unique = {i.url: i for i in items}
            usable = [i for i in unique.values() if i.price >= self.min_price]
            results.extend(usable[: self.max_per_store])
            log.info("%s: %d productos para «%s»", adapter.label, len(usable), query)

        return results, errors

    # ---------- detección sin historial ----------

    def detect(self, items: list[Found]) -> list[ListingAlert]:
        alerts: list[ListingAlert] = []
        seen: set[str] = set()

        for alert in self._detect_discounts(items) + self._detect_cross_store(items):
            if alert.found.url in seen:
                continue  # una alerta por producto, la más fuerte primero
            seen.add(alert.found.url)
            alerts.append(alert)

        alerts.sort(key=lambda a: a.drop_pct, reverse=True)
        return alerts

    def _detect_discounts(self, items: list[Found]) -> list[ListingAlert]:
        out = []
        for item in items:
            discount = item.discount_pct
            if discount is None or discount < self.min_discount_pct:
                continue
            out.append(
                ListingAlert(
                    found=item,
                    kind="descuento",
                    reference=item.reference_price or 0.0,
                    drop_pct=discount,
                    reason=(
                        f"{discount:.0f}% bajo el precio normal que publica la tienda "
                        f"({item.reference_price:,.0f}). En retail normal el máximo "
                        f"observado es ~75%."
                    ).replace(",", "."),
                )
            )
        return out

    def _detect_cross_store(self, items: list[Found]) -> list[ListingAlert]:
        """Agrupa anuncios que parecen el mismo producto y compara precios.

        Solo compara entre tiendas distintas: dos variantes de color de la misma
        tienda tienen títulos casi idénticos y precios legítimamente distintos.
        """
        groups = self._group_similar(items)
        out = []

        for group in groups:
            stores = {i.store for i in group}
            if len(stores) < 2 or len(group) < 2:
                continue

            prices = [i.price for i in group]
            median = statistics.median(prices)
            cheapest = min(group, key=lambda i: i.price)

            if median <= 0:
                continue
            drop = (median - cheapest.price) / median * 100.0
            if drop < self.min_cross_store_pct:
                continue

            others = ", ".join(
                sorted({i.store for i in group if i.store != cheapest.store})
            )
            out.append(
                ListingAlert(
                    found=cheapest,
                    kind="entre-tiendas",
                    reference=median,
                    drop_pct=drop,
                    reason=(
                        f"{drop:.0f}% más barato que el mismo producto en {others} "
                        f"(mediana {median:,.0f})"
                    ).replace(",", "."),
                )
            )
        return out

    def _group_similar(self, items: list[Found]) -> list[list[Found]]:
        """Agrupa por título parecido.

        Se indexa por palabra para no comparar todos contra todos: con ~120
        productos por categoría, la comparación completa sería 7.000 pares.
        """
        by_token: dict[str, list[int]] = defaultdict(list)
        token_sets = [tokens(i.name) for i in items]

        for idx, ts in enumerate(token_sets):
            for t in ts:
                by_token[t].append(idx)

        groups: list[list[Found]] = []
        assigned: set[int] = set()

        for idx, item in enumerate(items):
            if idx in assigned:
                continue
            candidates = {
                other
                for t in token_sets[idx]
                for other in by_token[t]
                if other != idx and other not in assigned
            }
            group = [item]
            assigned.add(idx)
            for other in candidates:
                if same_product(
                    item.name, items[other].name,
                    token_sets[idx], token_sets[other],
                    self.similarity_threshold,
                ):
                    group.append(items[other])
                    assigned.add(other)
            if len(group) > 1:
                groups.append(group)
        return groups
