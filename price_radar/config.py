from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent


def _exe_dir() -> Path | None:
    """Carpeta donde está el ejecutable, si corremos empaquetados."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return None


def data_dir() -> Path:
    """Dónde viven la configuración y la base de datos.

    Modo portátil primero: si junto al ejecutable hay un `config.yaml`, se usa
    esa carpeta. Es lo que permite instalar el programa con su configuración ya
    puesta, sin depender de %APPDATA%, que algunos entornos redirigen a una
    carpeta espejo (y entonces el programa lee una configuración distinta de la
    que se escribió, sin que nada falle a la vista).

    Si no, %APPDATA%\\PriceRadar. Nunca la carpeta del proyecto: la
    configuración lleva la contraseña de la base en la nube y un `git add` la
    publicaría.
    """
    junto_al_exe = _exe_dir()
    if junto_al_exe and (junto_al_exe / "config.yaml").exists():
        return junto_al_exe

    base = Path(os.environ.get("APPDATA") or Path.home() / ".config") / "PriceRadar"
    base.mkdir(parents=True, exist_ok=True)
    return base


def resource_path(name: str) -> Path:
    """Ruta a un archivo empaquetado (icono, plantillas), funcione o no congelado."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", ROOT)) / name
    return ROOT / name


CONFIG_PATH = data_dir() / "config.yaml"
DB_PATH = data_dir() / "data" / "prices.db"

DEFAULT_CONFIG: dict[str, Any] = {
    "scraper": {
        "user_agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        "timeout": 20,
        "delay_per_domain": 8,
        "respect_robots": True,
        "max_retries": 2,
    },
    "scheduler": {"interval_minutes": 30},
    "detector": {
        "min_history": 8,
        "min_drop_pct": 35.0,
        "mad_threshold": 4.0,
        "window_days": 30,
        "cooldown_hours": 12,
    },
    # Umbrales calibrados sobre 609 descuentos reales de 6 categorías:
    # mediana del retail 35%, máximo observado 74,9%. Ver discovery.py.
    "discovery": {
        "min_discount_pct": 80.0,
        "min_cross_store_pct": 45.0,
        "similarity_threshold": 0.6,
        "max_per_store": 60,
        "min_price": 1000.0,
    },
    # Vacío = base local. Con una cadena de Neon, la app de escritorio pasa a
    # ser un visor de lo que busca GitHub Actions en la nube.
    "cloud": {"database_url": "", "report_url": ""},
    "alerts": {"telegram": {"enabled": False, "bot_token": "", "chat_id": ""}},
    "web": {"host": "127.0.0.1", "port": 8000},
    "targets": [],
}


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


@dataclass
class Target:
    name: str
    url: str
    price_selector: str | None = None
    attr: str | None = None
    currency: str = "CLP"
    enabled: bool = True

    @property
    def key(self) -> str:
        """Identidad estable del producto en la base de datos."""
        return self.url


@dataclass
class Config:
    scraper: dict[str, Any] = field(default_factory=dict)
    scheduler: dict[str, Any] = field(default_factory=dict)
    detector: dict[str, Any] = field(default_factory=dict)
    discovery: dict[str, Any] = field(default_factory=dict)
    cloud: dict[str, Any] = field(default_factory=dict)
    alerts: dict[str, Any] = field(default_factory=dict)
    web: dict[str, Any] = field(default_factory=dict)
    targets: list[Target] = field(default_factory=list)

    path: Path = CONFIG_PATH

    @classmethod
    def load(cls, path: Path | str = CONFIG_PATH) -> "Config":
        path = Path(path)
        raw: dict[str, Any] = {}
        if path.exists():
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        merged = _merge(DEFAULT_CONFIG, raw)
        return cls(
            scraper=merged["scraper"],
            scheduler=merged["scheduler"],
            detector=merged["detector"],
            discovery=merged["discovery"],
            cloud=merged["cloud"],
            alerts=merged["alerts"],
            web=merged["web"],
            targets=[Target(**t) for t in merged.get("targets") or []],
            path=path,
        )

    def save(self) -> None:
        """Persiste solo los ajustes; los productos viven en la base de datos."""
        payload = {
            "scraper": self.scraper,
            "scheduler": self.scheduler,
            "detector": self.detector,
            "discovery": self.discovery,
            "cloud": self.cloud,
            "alerts": self.alerts,
            "web": self.web,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )

    @property
    def active_targets(self) -> list[Target]:
        return [t for t in self.targets if t.enabled]
