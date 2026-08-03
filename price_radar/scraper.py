from __future__ import annotations

import json
import logging
import re
import time
import urllib.robotparser as robotparser
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from .config import Target

log = logging.getLogger(__name__)

# Números tipo 1.299.990 / 1,299,990.50 / 1299990
_NUM_RE = re.compile(r"\d[\d.,\s]*\d|\d")


class ScrapeError(Exception):
    pass


def parse_price(text: str) -> float | None:
    """Extrae un número de un string de precio, tolerando separadores ES/EN."""
    if text is None:
        return None
    cleaned = str(text).replace("\xa0", " ").strip()
    m = _NUM_RE.search(cleaned)
    if not m:
        return None
    raw = m.group(0).replace(" ", "")

    if "," in raw and "." in raw:
        # El separador decimal es el que aparece más a la derecha.
        if raw.rfind(",") > raw.rfind("."):
            raw = raw.replace(".", "").replace(",", ".")
        else:
            raw = raw.replace(",", "")
    elif "," in raw:
        # "1,50" -> decimal ; "1,299,990" -> miles
        tail = raw.split(",")[-1]
        raw = raw.replace(",", ".") if len(tail) <= 2 and raw.count(",") == 1 else raw.replace(",", "")
    elif "." in raw:
        tail = raw.split(".")[-1]
        if not (len(tail) <= 2 and raw.count(".") == 1):
            raw = raw.replace(".", "")

    try:
        value = float(raw)
    except ValueError:
        return None
    return value if value > 0 else None


def _walk_jsonld(node: Any):
    """Recorre un blob JSON-LD buscando cualquier objeto con 'price'."""
    if isinstance(node, dict):
        for key in ("price", "lowPrice", "highPrice"):
            if key in node:
                value = parse_price(node[key])
                if value is not None:
                    yield value
        for value in node.values():
            yield from _walk_jsonld(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk_jsonld(item)


def autodetect_price(soup: BeautifulSoup) -> float | None:
    """Fallback cuando no hay selector: JSON-LD -> microdata -> OpenGraph."""
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        for value in _walk_jsonld(data):
            return value

    for attrs in (
        {"itemprop": "price"},
        {"property": "product:price:amount"},
        {"property": "og:price:amount"},
        {"name": "twitter:data1"},
    ):
        tag = soup.find(attrs=attrs)
        if tag:
            value = parse_price(tag.get("content") or tag.get_text())
            if value is not None:
                return value
    return None


@dataclass
class ScrapeResult:
    price: float | None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.price is not None


class Scraper:
    def __init__(self, settings: dict[str, Any]):
        self.user_agent = settings.get("user_agent", "PriceRadar/0.1")
        self.timeout = settings.get("timeout", 20)
        self.delay = settings.get("delay_per_domain", 8)
        self.respect_robots = settings.get("respect_robots", True)
        self.max_retries = settings.get("max_retries", 2)

        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": self.user_agent,
                "Accept-Language": "es-CL,es;q=0.9,en;q=0.8",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            }
        )
        self._last_hit: dict[str, float] = {}
        self._robots: dict[str, robotparser.RobotFileParser | None] = {}

    # ---------- cortesía ----------

    def _throttle(self, domain: str, delay: float | None = None) -> None:
        """Espera lo necesario para no golpear dos veces seguidas el mismo dominio.

        `delay` permite bajar la espera para archivos estáticos pensados para
        rastreadores (sitemaps), que no cuestan lo mismo que una búsqueda.
        """
        wait_for = self.delay if delay is None else delay
        last = self._last_hit.get(domain)
        if last is not None:
            wait = wait_for - (time.monotonic() - last)
            if wait > 0:
                time.sleep(wait)
        self._last_hit[domain] = time.monotonic()

    def _allowed(self, url: str) -> bool:
        if not self.respect_robots:
            return True
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin not in self._robots:
            # Se descarga con la sesión propia, no con rp.read(): urllib manda
            # el user-agent de Python, muchos CDN responden 403, y ante un 403
            # RobotFileParser asume "todo prohibido". Eso hacía que se
            # bloquearan sitios que en realidad permiten el acceso.
            rp: robotparser.RobotFileParser | None = None
            try:
                resp = self.session.get(f"{origin}/robots.txt", timeout=self.timeout)
                if resp.status_code == 200:
                    rp = robotparser.RobotFileParser()
                    rp.parse(resp.text.splitlines())
                elif resp.status_code in (401, 403):
                    log.debug("robots.txt de %s devolvió %s", origin, resp.status_code)
            except requests.RequestException as exc:
                log.debug("robots.txt no disponible en %s: %s", origin, exc)
            self._robots[origin] = rp
        rp = self._robots[origin]
        return True if rp is None else rp.can_fetch(self.user_agent, url)

    # ---------- fetch ----------

    def get_html(self, url: str, polite_delay: float | None = None) -> str:
        """Descarga una página respetando robots.txt y el delay por dominio.

        Lanza ScrapeError en vez de devolver None: quien pide un listado sí
        necesita distinguir entre 'no hay productos' y 'no pude entrar'.
        """
        if not self._allowed(url):
            raise ScrapeError("bloqueado por robots.txt")

        domain = urlparse(url).netloc
        last_error = "error desconocido"

        for attempt in range(self.max_retries + 1):
            self._throttle(domain, polite_delay)
            try:
                resp = self.session.get(url, timeout=self.timeout)
                if resp.status_code == 200:
                    return resp.text
                last_error = f"HTTP {resp.status_code}"
                if resp.status_code in (429, 503):
                    time.sleep(self.delay * (attempt + 1))
                    continue
                break
            except requests.RequestException as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                log.warning("fallo al pedir %s (intento %d): %s", url, attempt + 1, last_error)

        raise ScrapeError(last_error)

    def fetch(self, target: Target) -> ScrapeResult:
        if not self._allowed(target.url):
            return ScrapeResult(None, "bloqueado por robots.txt")

        domain = urlparse(target.url).netloc
        last_error = "error desconocido"

        for attempt in range(self.max_retries + 1):
            self._throttle(domain)
            try:
                resp = self.session.get(target.url, timeout=self.timeout)
                if resp.status_code != 200:
                    last_error = f"HTTP {resp.status_code}"
                    if resp.status_code in (429, 503):
                        time.sleep(self.delay * (attempt + 1))
                        continue
                    return ScrapeResult(None, last_error)
                return self._extract(resp.text, target)
            except requests.RequestException as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                log.warning("fallo al pedir %s (intento %d): %s", target.url, attempt + 1, last_error)

        return ScrapeResult(None, last_error)

    def _extract(self, html: str, target: Target) -> ScrapeResult:
        soup = BeautifulSoup(html, "lxml")

        if target.price_selector:
            node = soup.select_one(target.price_selector)
            if node is None:
                return ScrapeResult(None, f"selector sin coincidencias: {target.price_selector}")
            raw = node.get(target.attr) if target.attr else node.get_text(" ", strip=True)
            price = parse_price(raw)
            if price is None:
                return ScrapeResult(None, f"no se pudo parsear el precio desde: {raw!r}")
            return ScrapeResult(price)

        price = autodetect_price(soup)
        if price is None:
            return ScrapeResult(None, "autodetección falló; define price_selector")
        return ScrapeResult(price)
