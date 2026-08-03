"""Punto de entrada de la aplicación de escritorio (lo que se convierte en .exe)."""
from __future__ import annotations

import logging
import sys
import traceback
from pathlib import Path

from price_radar.config import data_dir
from price_radar.gui import PriceRadarApp


def main() -> int:
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
