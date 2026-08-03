"""Price Radar — vigilancia de precios y detección de errores de precio.

Uso:
    python main.py check     # un ciclo único y salir
    python main.py serve     # dashboard web + escaneo programado
    python main.py test-url <url> [selector]   # probar extracción de un precio
"""
from __future__ import annotations

import argparse
import logging
import sys

from price_radar.config import Config, Target
from price_radar.engine import Engine
from price_radar.scraper import Scraper


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def cmd_check(config: Config) -> int:
    engine = Engine(config)
    totals = engine.scan_all_categories(progress=lambda m: print("  ", m))
    manual = engine.run_cycle() if engine.targets() else {"alerts": 0}
    print(f"\nCategorías: {totals['categories']} | Productos: {totals['found']} | "
          f"Alertas: {totals['alerts'] + manual['alerts']}")
    for err in totals["errors"]:
        print(f"  aviso: {err}")
    return 0


def cmd_find(config: Config, query: str) -> int:
    """Busca una categoría una vez y muestra lo encontrado, sin guardar nada."""
    engine = Engine(config)
    items, errors = engine.discovery.search(query, progress=lambda m: print("  ", m))
    alerts = {a.found.url: a for a in engine.discovery.detect(items)}

    print(f"\n{len(items)} productos para «{query}»:\n")
    for item in sorted(items, key=lambda i: i.price):
        flag = "  ⚠ ALERTA" if item.url in alerts else ""
        print(f"  {item.store:11} {item.price:>11,.0f}  {item.name[:58]}{flag}")
    for err in errors:
        print(f"\n  aviso: {err}")
    print(f"\n{len(alerts)} posible(s) error(es) de precio.")
    for a in alerts.values():
        print(f"  [{a.kind}] {a.found.name[:50]} -> {a.reason}")
    return 0


def cmd_test_url(config: Config, url: str, selector: str | None) -> int:
    target = Target(name="test", url=url, price_selector=selector)
    result = Scraper(config.scraper).fetch(target)
    if result.ok:
        print(f"Precio detectado: {result.price:,.2f}")
        return 0
    print(f"No se pudo extraer el precio: {result.error}")
    return 1


def cmd_serve(config: Config) -> int:
    import uvicorn
    from apscheduler.schedulers.background import BackgroundScheduler

    from price_radar.web import create_app

    engine = Engine(config)
    interval = config.scheduler.get("interval_minutes", 30)

    scheduler = BackgroundScheduler()
    scheduler.add_job(engine.run_cycle, "interval", minutes=interval, id="scan",
                      max_instances=1, coalesce=True)
    scheduler.start()
    logging.info("Escaneo programado cada %d minutos", interval)

    host = config.web.get("host", "127.0.0.1")
    port = config.web.get("port", 8000)
    print(f"\nDashboard en http://{host}:{port}\n")
    try:
        uvicorn.run(create_app(config), host=host, port=port, log_level="warning")
    finally:
        scheduler.shutdown(wait=False)
    return 0


def main() -> int:
    setup_logging()
    parser = argparse.ArgumentParser(description="Price Radar")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="ejecuta un ciclo de escaneo y sale")
    sub.add_parser("serve", help="levanta el dashboard con escaneo programado")
    p_find = sub.add_parser("find", help="busca una categoría y muestra el resultado")
    p_find.add_argument("query")
    p_test = sub.add_parser("test-url", help="prueba la extracción de precio de una URL")
    p_test.add_argument("url")
    p_test.add_argument("selector", nargs="?", default=None)

    args = parser.parse_args()
    config = Config.load()

    if args.command == "check":
        return cmd_check(config)
    if args.command == "find":
        return cmd_find(config, args.query)
    if args.command == "serve":
        return cmd_serve(config)
    if args.command == "test-url":
        return cmd_test_url(config, args.url, args.selector)
    return 1


if __name__ == "__main__":
    sys.exit(main())
