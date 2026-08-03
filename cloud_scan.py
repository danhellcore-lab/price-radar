"""Ejecución en la nube: lo que corre GitHub Actions cada media hora.

No necesita tu computador encendido. Lee la configuración de variables de
entorno (los "secrets" del repositorio), busca las categorías, guarda todo en
PostgreSQL y publica un informe HTML para GitHub Pages.

Variables que espera:
  DATABASE_URL        cadena de conexión de Neon (obligatoria)
  TELEGRAM_BOT_TOKEN  token del bot (opcional)
  TELEGRAM_CHAT_ID    tu chat id (opcional)
  CATEGORIES          lista separada por comas, solo para la primera vez
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from price_radar.config import Config
from price_radar.engine import Engine
from price_radar.report import write_report
from price_radar.storage import Storage

log = logging.getLogger("cloud")


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        stream=sys.stdout,
    )

    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        print("ERROR: falta DATABASE_URL. Configúralo como secret del repositorio.")
        return 1

    config = Config.load()
    config.alerts["telegram"] = {
        "enabled": bool(os.environ.get("TELEGRAM_BOT_TOKEN")),
        "bot_token": os.environ.get("TELEGRAM_BOT_TOKEN", ""),
        "chat_id": os.environ.get("TELEGRAM_CHAT_ID", ""),
    }

    storage = Storage(url=database_url)
    log.info("Base de datos: %s", storage.location)

    # Siembra inicial: solo si la base está vacía, para que después mandes tú
    # desde la app y no se repongan categorías que borraste.
    if not storage.categories():
        seeds = [c.strip() for c in os.environ.get("CATEGORIES", "").split(",") if c.strip()]
        for seed in seeds:
            storage.add_category(seed)
        if seeds:
            log.info("Categorías iniciales: %s", ", ".join(seeds))

    if not storage.categories():
        log.warning("No hay categorías configuradas. Nada que hacer.")
    else:
        engine = Engine(config, storage)
        totals = engine.scan_all_categories(progress=lambda m: log.info("%s", m))
        log.info(
            "Ciclo terminado: %d categorías, %d productos, %d alertas",
            totals["categories"], totals["found"], totals["alerts"],
        )
        for err in totals["errors"]:
            log.warning("aviso: %s", err)

    out = Path(os.environ.get("REPORT_DIR", "public"))
    written = write_report(storage, out)
    log.info("Informe publicado en %s", written)
    return 0


if __name__ == "__main__":
    sys.exit(main())
