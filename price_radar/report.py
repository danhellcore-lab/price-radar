"""Genera el informe web estático que se publica en GitHub Pages.

Es una sola página sin servidor ni base de datos detrás: la publica cada
ejecución del cron. Los datos van incrustados como JSON y el filtrado, la
ordenación y la búsqueda ocurren en el navegador, así que responden al instante
y siguen sin necesitar backend.

No se usa `str.format` para armar la página porque el CSS y el JavaScript están
llenos de llaves; se sustituyen marcadores explícitos.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .storage import Storage

PAGE = r"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Price Radar</title>
<style>
  :root {
    --bg:#0f1115; --card:#171a21; --border:#262b36; --text:#e6e8ec;
    --muted:#8b93a1; --accent:#4da3ff; --alert:#ff5c5c; --ok:#3ecf8e;
    --input:#0f1319;
  }
  @media (prefers-color-scheme: light) {
    :root { --bg:#f6f7f9; --card:#fff; --border:#e2e5ea; --text:#1a1d23;
            --muted:#5b6472; --input:#fff; }
  }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--text);
         font:15px/1.55 system-ui,-apple-system,Segoe UI,sans-serif; }
  header { padding:1.1rem 1rem .9rem; border-bottom:1px solid var(--border); }
  h1 { margin:0; font-size:1.15rem; }
  .sub { color:var(--muted); font-size:.8rem; margin-top:.2rem; }
  main { max-width:1150px; margin:0 auto; padding:1rem; }
  h2 { font-size:.78rem; text-transform:uppercase; letter-spacing:.08em;
       color:var(--muted); margin:1.6rem 0 .6rem; }
  .stats { display:flex; gap:.5rem; flex-wrap:wrap; margin-top:.85rem; }
  .stat { background:var(--card); border:1px solid var(--border); border-radius:9px;
          padding:.5rem .85rem; flex:1; min-width:110px; }
  .stat b { display:block; font-size:1.25rem; }
  .stat span { color:var(--muted); font-size:.74rem; }
  .card { background:var(--card); border:1px solid var(--border); border-radius:10px;
          padding:.8rem 1rem; margin-bottom:.55rem; }
  .alert { border-left:3px solid var(--alert); }
  .row { display:flex; justify-content:space-between; gap:1rem; align-items:flex-start; }
  .name { font-weight:600; }
  .meta { color:var(--muted); font-size:.79rem; }
  .price { font-size:1.15rem; font-weight:700; white-space:nowrap; }
  .tag { font-size:.68rem; padding:.1rem .4rem; border-radius:4px;
         background:var(--alert); color:#fff; }
  a { color:var(--accent); text-decoration:none; }
  .empty { color:var(--muted); font-style:italic; }

  /* --- filtros --- */
  .filters { background:var(--card); border:1px solid var(--border);
             border-radius:10px; padding:.85rem; margin-bottom:.8rem; }
  .fgrid { display:grid; grid-template-columns:repeat(auto-fit,minmax(165px,1fr));
           gap:.6rem; }
  .field { display:flex; flex-direction:column; gap:.22rem; }
  .field label { font-size:.7rem; text-transform:uppercase; letter-spacing:.05em;
                 color:var(--muted); }
  input, select { background:var(--input); color:var(--text);
                  border:1px solid var(--border); border-radius:7px;
                  padding:.45rem .55rem; font-size:.9rem; font-family:inherit;
                  width:100%; }
  input:focus, select:focus { outline:2px solid var(--accent); outline-offset:-1px; }
  .search { font-size:1rem; padding:.55rem .7rem; }
  .fbar { display:flex; justify-content:space-between; align-items:center;
          gap:1rem; margin-top:.7rem; flex-wrap:wrap; }
  .count { color:var(--muted); font-size:.82rem; }
  button.link { background:none; border:none; color:var(--accent); cursor:pointer;
                font-size:.82rem; padding:0; font-family:inherit; }

  /* --- tabla --- */
  .wrap { overflow-x:auto; border:1px solid var(--border); border-radius:10px;
          background:var(--card); }
  table { width:100%; border-collapse:collapse; font-size:.87rem; }
  th, td { text-align:left; padding:.5rem .6rem; border-bottom:1px solid var(--border);
           vertical-align:top; }
  th { color:var(--muted); font-weight:600; font-size:.71rem; text-transform:uppercase;
       position:sticky; top:0; background:var(--card); cursor:pointer;
       white-space:nowrap; user-select:none; }
  th:hover { color:var(--text); }
  tr:last-child td { border-bottom:none; }
  td.num { text-align:right; white-space:nowrap; font-variant-numeric:tabular-nums; }
  .was { color:var(--muted); text-decoration:line-through; font-size:.8rem; }
  .pct { font-weight:700; }
  .pct.hi { color:var(--alert); }
  .chip { font-size:.7rem; color:var(--muted); border:1px solid var(--border);
          border-radius:4px; padding:.05rem .35rem; white-space:nowrap; }
  .more { text-align:center; padding:.9rem; }
  @media (max-width:640px) {
    .hide-sm { display:none; }
    main { padding:.7rem; }
  }
</style>
</head>
<body>
<header>
  <h1>📡 Price Radar</h1>
  <div class="sub">Última revisión: __UPDATED__ · se actualiza solo cada __INTERVAL__ minutos</div>
</header>
<main>
  <div class="stats">
    <div class="stat"><b>__N_CATEGORIES__</b><span>categorías</span></div>
    <div class="stat"><b>__N_PRODUCTS__</b><span>productos vigilados</span></div>
    <div class="stat"><b>__N_STORES__</b><span>tiendas</span></div>
    <div class="stat"><b>__N_ALERTS__</b><span>alertas</span></div>
  </div>

  <h2>Alertas</h2>
  <div id="alerts"></div>

  <h2>Todos los productos</h2>
  <div class="filters">
    <div class="field">
      <label for="q">Buscar producto</label>
      <input id="q" class="search" type="search" placeholder="escribe: notebook, samsung, rtx…"
             autocomplete="off">
    </div>
    <div class="fgrid" style="margin-top:.6rem">
      <div class="field">
        <label for="cat">Categoría</label>
        <select id="cat"><option value="">Todas</option></select>
      </div>
      <div class="field">
        <label for="store">Tienda</label>
        <select id="store"><option value="">Todas</option></select>
      </div>
      <div class="field">
        <label for="minPct">Descuento mínimo</label>
        <select id="minPct">
          <option value="0">Cualquiera</option>
          <option value="20">20% o más</option>
          <option value="40">40% o más</option>
          <option value="50">50% o más</option>
          <option value="60">60% o más</option>
          <option value="70">70% o más</option>
          <option value="80">80% o más (error de precio)</option>
        </select>
      </div>
      <div class="field">
        <label for="maxPrice">Precio máximo</label>
        <input id="maxPrice" type="number" inputmode="numeric" placeholder="ej: 5000000">
      </div>
      <div class="field">
        <label for="minPrice">Precio mínimo</label>
        <input id="minPrice" type="number" inputmode="numeric" placeholder="ej: 10000">
      </div>
      <div class="field">
        <label for="sort">Ordenar por</label>
        <select id="sort">
          <option value="disc-desc">Mayor descuento</option>
          <option value="price-asc">Precio: menor a mayor</option>
          <option value="price-desc">Precio: mayor a menor</option>
          <option value="name-asc">Nombre (A-Z)</option>
          <option value="store-asc">Tienda</option>
          <option value="cat-asc">Categoría</option>
        </select>
      </div>
    </div>
    <div class="fbar">
      <span class="count" id="count"></span>
      <button class="link" id="reset">Limpiar filtros</button>
    </div>
  </div>

  <div class="wrap">
    <table>
      <thead><tr>
        <th data-sort="name">Producto</th>
        <th data-sort="cat" class="hide-sm">Categoría</th>
        <th data-sort="store" class="hide-sm">Tienda</th>
        <th data-sort="price" class="num">Precio</th>
        <th data-sort="ref" class="num hide-sm">Antes</th>
        <th data-sort="disc" class="num">Desc.</th>
        <th></th>
      </tr></thead>
      <tbody id="rows"></tbody>
    </table>
    <div class="more" id="more"></div>
  </div>
</main>

<script id="datos" type="application/json">__DATA__</script>
<script>
const DATOS = JSON.parse(document.getElementById("datos").textContent);
const PRODUCTOS = DATOS.productos;
const ALERTAS = DATOS.alertas;
const POR_PAGINA = 200;

const $ = id => document.getElementById(id);
const money = v => v == null ? "—" : "$" + Math.round(v).toLocaleString("es-CL");
const esc = s => String(s ?? "").replace(/[&<>"']/g,
  c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));

let mostrando = POR_PAGINA;

/* Las preferencias se recuerdan: si siempre ocultas lo que cuesta más de
   5 millones, no tiene sentido volver a escribirlo cada visita. */
const guardar = () => localStorage.setItem("priceradar-filtros", JSON.stringify({
  q: $("q").value, cat: $("cat").value, store: $("store").value,
  minPct: $("minPct").value, maxPrice: $("maxPrice").value,
  minPrice: $("minPrice").value, sort: $("sort").value,
}));

function restaurar() {
  try {
    const f = JSON.parse(localStorage.getItem("priceradar-filtros") || "{}");
    for (const [k, v] of Object.entries(f)) if ($(k) && v != null) $(k).value = v;
  } catch (e) { /* preferencias corruptas: se ignoran */ }
}

function opciones(select, valores) {
  for (const v of valores) {
    const o = document.createElement("option");
    o.value = v;
    o.textContent = v.charAt(0).toUpperCase() + v.slice(1);
    select.appendChild(o);
  }
}

function filtrar() {
  const q = $("q").value.trim().toLowerCase();
  const palabras = q ? q.split(/\s+/) : [];
  const cat = $("cat").value, store = $("store").value;
  const minPct = parseFloat($("minPct").value) || 0;
  const maxPrice = parseFloat($("maxPrice").value);
  const minPrice = parseFloat($("minPrice").value);

  let lista = PRODUCTOS.filter(p => {
    // Todas las palabras deben aparecer, en cualquier orden.
    if (palabras.length) {
      const texto = (p.name + " " + p.store + " " + p.cat).toLowerCase();
      if (!palabras.every(w => texto.includes(w))) return false;
    }
    if (cat && p.cat !== cat) return false;
    if (store && p.store !== store) return false;
    if (minPct && (p.disc || 0) < minPct) return false;
    if (!isNaN(maxPrice) && p.price > maxPrice) return false;
    if (!isNaN(minPrice) && p.price < minPrice) return false;
    return true;
  });

  const [campo, dir] = $("sort").value.split("-");
  const signo = dir === "desc" ? -1 : 1;
  lista.sort((a, b) => {
    let x, y;
    if (campo === "disc") { x = a.disc || 0; y = b.disc || 0; }
    else if (campo === "price") { x = a.price; y = b.price; }
    else if (campo === "ref") { x = a.ref || 0; y = b.ref || 0; }
    else { x = String(a[campo === "name" ? "name" : campo]).toLowerCase();
           y = String(b[campo === "name" ? "name" : campo]).toLowerCase(); }
    return x < y ? -signo : x > y ? signo : 0;
  });
  return lista;
}

function pintar() {
  const lista = filtrar();
  const visibles = lista.slice(0, mostrando);

  $("rows").innerHTML = visibles.map(p => `<tr>
    <td><a href="${esc(p.url)}" target="_blank" rel="noopener noreferrer">${esc(p.name)}</a></td>
    <td class="hide-sm"><span class="chip">${esc(p.cat)}</span></td>
    <td class="hide-sm">${esc(p.store)}</td>
    <td class="num">${money(p.price)}</td>
    <td class="num hide-sm was">${p.ref ? money(p.ref) : ""}</td>
    <td class="num pct ${p.disc >= 60 ? "hi" : ""}">${p.disc ? "-" + p.disc.toFixed(0) + "%" : ""}</td>
    <td><a href="${esc(p.url)}" target="_blank" rel="noopener noreferrer">abrir</a></td>
  </tr>`).join("") ||
    `<tr><td colspan="7" class="empty" style="padding:1.5rem;text-align:center">
       Ningún producto coincide con esos filtros.</td></tr>`;

  $("count").textContent = lista.length === PRODUCTOS.length
    ? `${lista.length} productos`
    : `${lista.length} de ${PRODUCTOS.length} productos`;

  $("more").innerHTML = lista.length > mostrando
    ? `<button class="link" id="verMas">Ver ${Math.min(POR_PAGINA, lista.length - mostrando)} más
       (quedan ${lista.length - mostrando})</button>`
    : "";
  const btn = $("verMas");
  if (btn) btn.onclick = () => { mostrando += POR_PAGINA; pintar(); };

  guardar();
}

function pintarAlertas() {
  $("alerts").innerHTML = ALERTAS.length ? ALERTAS.map(a => `
    <div class="card alert"><div class="row"><div>
      <span class="tag">-${a.drop_pct.toFixed(0)}%</span>
      <span class="name">${esc(a.name)}</span>
      <div class="meta">${esc(a.reason)}</div>
      <div class="meta">${esc(a.store)} · ${esc(a.ts).slice(0, 16).replace("T", " ")}</div>
      <a href="${esc(a.url)}" target="_blank" rel="noopener noreferrer">ver en la tienda</a>
    </div><div class="price">${money(a.price)}</div></div></div>`).join("")
    : `<div class="card empty">Sin alertas todavía. Es lo normal:
       los errores de precio son poco frecuentes.</div>`;
}

opciones($("cat"), [...new Set(PRODUCTOS.map(p => p.cat))].sort());
opciones($("store"), [...new Set(PRODUCTOS.map(p => p.store))].sort());
restaurar();
pintarAlertas();
pintar();

for (const id of ["q", "cat", "store", "minPct", "maxPrice", "minPrice", "sort"]) {
  $(id).addEventListener("input", () => { mostrando = POR_PAGINA; pintar(); });
}
$("reset").onclick = () => {
  for (const id of ["q", "maxPrice", "minPrice"]) $(id).value = "";
  $("cat").value = ""; $("store").value = ""; $("minPct").value = "0";
  $("sort").value = "disc-desc";
  mostrando = POR_PAGINA;
  pintar();
};

// Clic en el encabezado: ordena por esa columna y alterna el sentido.
document.querySelectorAll("th[data-sort]").forEach(th => {
  th.onclick = () => {
    const campo = th.dataset.sort;
    const actual = $("sort").value;
    const desc = actual === `${campo}-asc`;
    const opcion = `${campo}-${desc ? "desc" : "asc"}`;
    if (![...$("sort").options].some(o => o.value === opcion)) {
      const o = document.createElement("option");
      o.value = opcion;
      o.textContent = th.textContent.trim() + (desc ? " ↓" : " ↑");
      $("sort").appendChild(o);
    }
    $("sort").value = opcion;
    mostrando = POR_PAGINA;
    pintar();
  };
});
</script>
</body>
</html>
"""


def collect(storage: Storage) -> dict[str, Any]:
    products = storage.products()
    latest = storage.latest_observations()
    alerts = storage.recent_alerts(60)

    filas = []
    for p in products:
        obs = latest.get(int(p["id"]))
        if not obs or not obs.get("price"):
            continue
        price = float(obs["price"])
        ref = float(obs["ref_price"]) if obs.get("ref_price") else None
        disc = (ref - price) / ref * 100.0 if ref and ref > price else None
        filas.append({
            "name": p["name"],
            "url": p["url"],
            "store": p["store"] or "manual",
            "cat": p["category"] or "sin categoría",
            "price": price,
            "ref": ref,
            "disc": disc,
        })

    filas.sort(key=lambda r: r["disc"] or 0, reverse=True)

    return {
        "productos": filas,
        "alertas": [
            {
                "name": a["name"], "url": a["url"], "store": (a["store"] or "").capitalize(),
                "price": float(a["price"]), "drop_pct": float(a["drop_pct"]),
                "reason": a["reason"], "ts": str(a["ts"]),
            }
            for a in alerts
        ],
        "categorias": [c["query"] for c in storage.categories()],
        "tiendas": sorted({r["store"] for r in filas}),
    }


def write_report(storage: Storage, out_dir: Path, interval_minutes: int = 30) -> Path:
    data = collect(storage)
    out_dir.mkdir(parents=True, exist_ok=True)

    # `</script>` dentro del JSON cerraría la etiqueta antes de tiempo y rompería
    # la página; se escapa la barra.
    payload = json.dumps(data, ensure_ascii=False, default=str).replace("</", "<\\/")

    page = PAGE
    for marcador, valor in (
        ("__UPDATED__", datetime.now(timezone.utc).strftime("%d-%m-%Y %H:%M UTC")),
        ("__INTERVAL__", str(interval_minutes)),
        ("__N_CATEGORIES__", str(len(data["categorias"]))),
        ("__N_PRODUCTS__", str(len(data["productos"]))),
        ("__N_STORES__", str(len(data["tiendas"]))),
        ("__N_ALERTS__", str(len(data["alertas"]))),
        ("__DATA__", payload),
    ):
        page = page.replace(marcador, valor)

    index = out_dir / "index.html"
    index.write_text(page, encoding="utf-8")

    (out_dir / "data.json").write_text(
        json.dumps({"generated": datetime.now(timezone.utc).isoformat(), **data},
                   ensure_ascii=False, indent=1, default=str),
        encoding="utf-8",
    )
    return index
