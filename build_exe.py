"""Genera PriceRadar.exe con PyInstaller.

Uso:  .venv\\Scripts\\python.exe build_exe.py
Resultado: dist/PriceRadar.exe (un solo archivo, sin instalación)
"""
from __future__ import annotations

import os
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
    instalar(exe)
    return 0


def instalar(exe: Path) -> None:
    """Instala el programa y crea los accesos directos.

    El ejecutable NO va en el Escritorio: allí se borra sin querer con
    facilidad y desaparece el programa entero. Va a una carpeta propia, y en el
    Escritorio y el menú Inicio quedan accesos directos, que si se borran no se
    llevan nada por delante.
    """
    destino = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "PriceRadar"
    destino.mkdir(parents=True, exist_ok=True)
    instalado = destino / "PriceRadar.exe"

    try:
        shutil.copy2(exe, instalado)
        print(f"Instalado en: {instalado}")
    except OSError as exc:
        print(f"No pude instalarlo ({exc}); usa el de dist\\.")
        return

    icono = ROOT / "icon.ico"
    if icono.exists():
        shutil.copy2(icono, destino / "icon.ico")

    escritorio = Path.home() / "Desktop"
    menu = (Path(os.environ.get("APPDATA", Path.home())) / "Microsoft" / "Windows" /
            "Start Menu" / "Programs")
    for carpeta, etiqueta in ((escritorio, "Escritorio"), (menu, "menú Inicio")):
        if not carpeta.is_dir():
            continue
        try:
            crear_acceso_directo(carpeta / "Price Radar.lnk", instalado, destino)
            print(f"Acceso directo en el {etiqueta}")
        except Exception as exc:
            print(f"No pude crear el acceso directo en el {etiqueta}: {exc}")


def crear_acceso_directo(destino_lnk: Path, exe: Path, carpeta_trabajo: Path) -> None:
    ps = (
        "$w = New-Object -ComObject WScript.Shell; "
        f"$s = $w.CreateShortcut('{destino_lnk}'); "
        f"$s.TargetPath = '{exe}'; "
        f"$s.WorkingDirectory = '{carpeta_trabajo}'; "
        f"$s.IconLocation = '{exe},0'; "
        "$s.Description = 'Price Radar - caza errores de precio'; "
        "$s.Save()"
    )
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True,
                   capture_output=True)


if __name__ == "__main__":
    sys.exit(main())
