"""Genera el informe web estático que se publica en GitHub Pages.

Es una sola página sin servidor ni base de datos detrás: la publica cada
ejecución del cron. Así se puede mirar desde el celular sin pagar hosting.
"""
from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .storage import Storage

PAGE = """<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Price Radar</title>
<style>
  :root {{
    --bg:#0f1115; --card:#171a21; --border:#262b36; --text:#e6e8ec;
    --muted:#8b93a1; --accent:#4da3ff; --alert:#ff5c5c; --ok:#3ecf8e;
  }}
  @media (prefers-color-scheme: light) {{
    :root {{ --bg:#f6f7f9; --card:#fff; --border:#e2e5ea; --text:#1a1d23; --muted:#6b7280; }}
  }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--text);
         font:15px/1.55 system-ui,-apple-system,Segoe UI,sans-serif; }}
  header {{ padding:1.25rem 1rem; border-bottom:1px solid var(--border); }}
  h1 {{ margin:0; font-size:1.15rem; }}
  .sub {{ color:var(--muted); font-size:.82rem; margin-top:.25rem; }}
  main {{ max-width:900px; margin:0 auto; padding:1rem; }}
  h2 {{ font-size:.78rem; text-transform:uppercase; letter-spacing:.08em;
        color:var(--muted); margin:1.75rem 0 .6rem; }}
  .stats {{ display:flex; gap:.6rem; flex-wrap:wrap; margin-top:.9rem; }}
  .stat {{ background:var(--card); border:1px solid var(--border); border-radius:9px;
           padding:.6rem .9rem; flex:1; min-width:120px; }}
  .stat b {{ display:block; font-size:1.3rem; }}
  .stat span {{ color:var(--muted); font-size:.76rem; }}
  .card {{ background:var(--card); border:1px solid var(--border); border-radius:10px;
           padding:.85rem 1rem; margin-bottom:.6rem; }}
  .alert {{ border-left:3px solid var(--alert); }}
  .row {{ display:flex; justify-content:space-between; gap:1rem; align-items:flex-start; }}
  .name {{ font-weight:600; }}
  .meta {{ color:var(--muted); font-size:.8rem; }}
  .price {{ font-size:1.15rem; font-weight:700; white-space:nowrap; }}
  .tag {{ font-size:.68rem; padding:.1rem .4rem; border-radius:4px;
          background:var(--alert); color:#fff; }}
  a {{ color:var(--accent); text-decoration:none; word-break:break-all; font-size:.8rem; }}
  .empty {{ color:var(--muted); font-style:italic; }}
  table {{ width:100%; border-collapse:collapse; font-size:.86rem; }}
  th, td {{ text-align:left; padding:.45rem .5rem; border-bottom:1px solid var(--border); }}
  th {{ color:var(--muted); font-weight:600; font-size:.74rem; text-transform:uppercase; }}
  .wrap {{ overflow-x:auto; }}
</style>
</head>
<body>
<header>
  <h1>📡 Price Radar</h1>
  <div class="sub">Última revisión: {updated} · se actualiza solo cada {interval} minutos</div>
</header>
<main>
  <div class="stats">
    <div class="stat"><b>{n_categories}</b><span>categorías</span></div>
    <div class="stat"><b>{n_products}</b><span>productos vigilados</span></div>
    <div class="stat"><b>{n_stores}</b><span>tiendas</span></div>
    <div class="stat"><b>{n_alerts}</b><span>alertas (7 días)</span></div>
  </div>

  <h2>Alertas</h2>
  {alerts}

  <h2>Los 25 mayores descuentos ahora mismo</h2>
  <div class="wrap">{deals}</div>
</main>
</body>
</html>
"""


def money(value: Any) -> str:
    if value is None:
        return "—"
    return "$" + f"{float(value):,.0f}".replace(",", ".")


def _alert_cards(alerts: list[dict[str, Any]]) -> str:
    if not alerts:
        return ('<div class="card empty">Sin alertas todavía. '
                "Es lo normal: los errores de precio son poco frecuentes.</div>")
    out = []
    for a in alerts:
        out.append(f"""<div class="card alert">
  <div class="row">
    <div>
      <span class="tag">-{a['drop_pct']:.0f}%</span>
      <span class="name">{html.escape(str(a['name']))}</span>
      <div class="meta">{html.escape(str(a['reason']))}</div>
      <div class="meta">{html.escape(str(a['store']).capitalize())} · {a['ts'][:16].replace('T', ' ')}</div>
      <a href="{html.escape(str(a['url']), quote=True)}" target="_blank" rel="noopener noreferrer">ver en la tienda</a>
    </div>
    <div class="price">{money(a['price'])}</div>
  </div>
</div>""")
    return "\n".join(out)


def _deals_table(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return '<div class="card empty">Todavía no hay datos.</div>'
    body = "\n".join(
        f"<tr><td>{html.escape(str(r['name']))[:70]}</td>"
        f"<td>{html.escape(str(r['store']).capitalize())}</td>"
        f"<td>{money(r['price'])}</td>"
        f"<td>{money(r['ref_price'])}</td>"
        f"<td>-{r['discount']:.0f}%</td>"
        f"<td><a href=\"{html.escape(str(r['url']), quote=True)}\" target=\"_blank\" "
        f"rel=\"noopener noreferrer\">abrir</a></td></tr>"
        for r in rows
    )
    return ("<table><thead><tr><th>Producto</th><th>Tienda</th><th>Precio</th>"
            "<th>Antes</th><th>Desc.</th><th></th></tr></thead>"
            f"<tbody>{body}</tbody></table>")


def collect(storage: Storage) -> dict[str, Any]:
    products = storage.products()
    latest = storage.latest_observations()
    alerts = storage.recent_alerts(40)

    deals = []
    for p in products:
        obs = latest.get(int(p["id"]))
        if not obs or not obs.get("price") or not obs.get("ref_price"):
            continue
        price, ref = float(obs["price"]), float(obs["ref_price"])
        if ref <= price:
            continue
        deals.append({
            "name": p["name"], "store": p["store"], "url": p["url"],
            "price": price, "ref_price": ref,
            "discount": (ref - price) / ref * 100.0,
        })
    deals.sort(key=lambda d: d["discount"], reverse=True)

    return {
        "products": products,
        "alerts": alerts,
        "deals": deals[:25],
        "stores": sorted({p["store"] for p in products if p["store"]}),
        "categories": storage.categories(),
    }


def write_report(storage: Storage, out_dir: Path, interval_minutes: int = 30) -> Path:
    data = collect(storage)
    out_dir.mkdir(parents=True, exist_ok=True)

    page = PAGE.format(
        updated=datetime.now(timezone.utc).strftime("%d-%m-%Y %H:%M UTC"),
        interval=interval_minutes,
        n_categories=len(data["categories"]),
        n_products=len(data["products"]),
        n_stores=len(data["stores"]),
        n_alerts=len(data["alerts"]),
        alerts=_alert_cards(data["alerts"]),
        deals=_deals_table(data["deals"]),
    )

    index = out_dir / "index.html"
    index.write_text(page, encoding="utf-8")

    # Los mismos datos en JSON, por si luego quieres consumirlos desde otro sitio.
    (out_dir / "data.json").write_text(
        json.dumps(
            {"generated": datetime.now(timezone.utc).isoformat(),
             "alerts": data["alerts"], "deals": data["deals"]},
            ensure_ascii=False, indent=1, default=str,
        ),
        encoding="utf-8",
    )
    return index
