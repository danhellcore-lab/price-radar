"""Punto de entrada de la aplicación de escritorio (lo que se convierte en .exe)."""
from __future__ import annotations

import logging
import sys
import traceback
from pathlib import Path

from price_radar.config import data_dir
from price_radar.gui import PriceRadarApp


def check() -> int:
    """`PriceRadar.exe --check`: comprueba que el ejecutable está completo.

    Existe porque dentro de un .exe empaquetado no hay forma de probar a mano
    si un driver quedó incluido; sin esto, un fallo del empaquetado solo se
    descubre el día que el usuario configura la nube.
    """
    lines = [f"Price Radar — diagnóstico", f"Carpeta de datos: {data_dir()}"]
    ok = True

    for label, module in (
        ("Lectura de páginas (requests)", "requests"),
        ("Análisis de HTML (bs4/lxml)", "bs4"),
        ("Interfaz gráfica (tkinter)", "tkinter"),
        ("Base de datos en la nube (psycopg)", "psycopg"),
        ("Configuración (yaml)", "yaml"),
    ):
        try:
            __import__(module)
            lines.append(f"  OK   {label}")
        except Exception as exc:
            ok = False
            lines.append(f"  FALLA {label}: {exc}")

    try:
        import psycopg

        # Que importe no basta: la parte binaria se carga al conectar.
        try:
            psycopg.connect("postgresql://u:p@127.0.0.1:1/x", connect_timeout=2)
        except psycopg.OperationalError:
            lines.append("  OK   El driver de PostgreSQL funciona (rechazó una conexión falsa)")
        except ImportError as exc:
            ok = False
            lines.append(f"  FALLA Falta la parte binaria de psycopg: {exc}")
    except Exception as exc:  # pragma: no cover
        lines.append(f"  aviso: no se pudo probar psycopg a fondo ({exc})")

    lines.append("RESULTADO: todo correcto" if ok else "RESULTADO: hay componentes ausentes")
    report = "\n".join(lines)
    print(report)
    (Path(data_dir()) / "diagnostico.txt").write_text(report, encoding="utf-8")
    return 0 if ok else 1


def main() -> int:
    if "--check" in sys.argv:
        return check()

    log_path = Path(data_dir()) / "price-radar.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        handlers=[logging.FileHandler(log_path, encoding="utf-8")],
    )
    try:
        PriceRadarApp().run()
    except Exception:
        logging.exception("La aplicación terminó con un error")
        try:
            from tkinter import messagebox

            messagebox.showerror(
                "Price Radar",
                "La aplicación tuvo un problema y debe cerrarse.\n\n"
                f"Los detalles están en:\n{log_path}",
            )
        except Exception:
            traceback.print_exc()
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
