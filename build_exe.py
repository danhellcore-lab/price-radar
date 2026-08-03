"""Genera PriceRadar.exe con PyInstaller.

Uso:  .venv\\Scripts\\python.exe build_exe.py
Resultado: dist/PriceRadar.exe (un solo archivo, sin instalación)
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main() -> int:
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("Falta PyInstaller. Instálalo con:  pip install pyinstaller")
        return 1

    icon = ROOT / "icon.ico"
    if not icon.exists():
        print("Generando el logo…")
        subprocess.run([sys.executable, str(ROOT / "make_icon.py")], cwd=ROOT, check=True)

    for folder in ("build", "dist"):
        shutil.rmtree(ROOT / folder, ignore_errors=True)
    if (ROOT / "dist" / "PriceRadar.exe").exists():
        print("No pude borrar dist\\PriceRadar.exe — ciérralo si está abierto.")
        return 1

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--onefile",
        "--windowed",                 # sin ventana negra de consola
        "--name", "PriceRadar",
        "--icon", str(icon),
        # El dashboard web comparte el paquete y necesita su plantilla.
        "--add-data", f"{ROOT / 'price_radar' / 'templates'};price_radar/templates",
        # El icono también va dentro, para la ventana y la barra de tareas.
        "--add-data", f"{icon};.",
        # PyInstaller no ve estos imports porque son indirectos.
        "--hidden-import", "bs4",
        "--hidden-import", "lxml._elementpath",
        "--collect-submodules", "apscheduler",
        # El driver de PostgreSQL se carga por nombre al conectar a la nube;
        # PyInstaller no lo ve si no se le dice.
        "--collect-all", "psycopg",
        "--collect-all", "psycopg_binary",
        str(ROOT / "app.py"),
    ]

    print("Compilando…")
    result = subprocess.run(cmd, cwd=ROOT)
    if result.returncode != 0:
        return result.returncode

    exe = ROOT / "dist" / "PriceRadar.exe"
    size = exe.stat().st_size / 1024 / 1024
    print(f"\nListo: {exe}  ({size:.1f} MB)")

    # Copiarlo al Escritorio es lo que hace que sea "doble clic y ya".
    desktop = Path.home() / "Desktop"
    if desktop.is_dir():
        try:
            shutil.copy2(exe, desktop / "PriceRadar.exe")
            print(f"Copiado al Escritorio: {desktop / 'PriceRadar.exe'}")
        except OSError as exc:
            print(f"No pude copiarlo al Escritorio ({exc}). Hazlo a mano desde dist\\.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
