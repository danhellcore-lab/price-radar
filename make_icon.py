"""Genera el logo de Price Radar (icon.ico + logo.png).

Se dibuja a 1024px y se reduce a cada tamaño del .ico, para que el icono se vea
nítido tanto en el escritorio como en la barra de tareas a 16px.

Uso:  .venv\\Scripts\\python.exe make_icon.py
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent
S = 1024  # lienzo de trabajo

BG_TOP = (37, 99, 235)      # azul
BG_BOTTOM = (14, 40, 110)   # azul profundo
SWEEP = (56, 189, 248)      # cian del radar
ARROW = (52, 211, 153)      # verde: el precio baja


def rounded_gradient(size: int, radius: int) -> Image.Image:
    """Fondo con degradado vertical y esquinas redondeadas."""
    grad = Image.new("RGB", (1, size))
    for y in range(size):
        t = y / max(size - 1, 1)
        grad.putpixel(
            (0, y),
            tuple(int(a + (b - a) * t) for a, b in zip(BG_TOP, BG_BOTTOM)),
        )
    grad = grad.resize((size, size))

    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=255)

    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(grad, (0, 0), mask)
    return out


def draw_logo() -> Image.Image:
    img = rounded_gradient(S, radius=int(S * 0.22))
    d = ImageDraw.Draw(img)

    cx, cy = S * 0.5, S * 0.56  # centro del radar, algo bajo para dejar aire arriba

    # Arcos del radar: tres ondas concéntricas abiertas hacia arriba.
    for i, radius_f in enumerate((0.20, 0.30, 0.40)):
        r = S * radius_f
        width = int(S * (0.045 - i * 0.008))
        alpha = 255 - i * 55
        d.arc(
            [cx - r, cy - r, cx + r, cy + r],
            start=200, end=340,
            fill=SWEEP + (alpha,),
            width=width,
        )

    # Flecha hacia abajo = caída de precio, que es lo que la app busca.
    shaft_w = S * 0.075
    top = cy - S * 0.03
    tip = cy + S * 0.30
    head_w = S * 0.20
    head_top = cy + S * 0.14

    d.polygon(
        [
            (cx - shaft_w / 2, top),
            (cx + shaft_w / 2, top),
            (cx + shaft_w / 2, head_top),
            (cx + head_w / 2, head_top),
            (cx, tip),
            (cx - head_w / 2, head_top),
            (cx - shaft_w / 2, head_top),
        ],
        fill=ARROW + (255,),
    )

    return img


def main() -> None:
    logo = draw_logo()

    png = ROOT / "logo.png"
    logo.resize((512, 512), Image.LANCZOS).save(png)

    sizes = [(s, s) for s in (16, 24, 32, 48, 64, 128, 256)]
    ico = ROOT / "icon.ico"
    # Reduzco cada tamaño desde el original de 1024 (Pillow lo hace internamente
    # al recibir `sizes`, usando el mejor remuestreo disponible).
    logo.save(ico, format="ICO", sizes=sizes)

    print(f"Creado: {ico}")
    print(f"Creado: {png}")


if __name__ == "__main__":
    main()
