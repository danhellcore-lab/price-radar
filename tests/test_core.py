"""Pruebas de las piezas críticas: parseo, detección y emparejamiento.

No tocan la red: los adaptadores se prueban con HTML guardado, para que la
suite no dependa de que una tienda esté disponible.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bs4 import BeautifulSoup

from price_radar.alerts import explain, format_alert
from price_radar.detector import Detector
from price_radar.discovery import Discovery, model_codes, same_product, tokens
from price_radar.scraper import autodetect_price, parse_price
from price_radar.stores import Falabella, Found, Paris, Ripley, Sodimac

failures: list[str] = []


def check(label: str, got, expected) -> None:
    if got != expected:
        failures.append(f"{label}: esperado {expected!r}, obtenido {got!r}")


# ---------- parseo de precios ----------
check("CLP puntos", parse_price("$1.299.990"), 1299990.0)
check("USD comas", parse_price("US$ 1,299,990"), 1299990.0)
check("decimal EN", parse_price("$1,299.50"), 1299.5)
check("decimal ES", parse_price("1.299,50 €"), 1299.5)
check("decimal coma corta", parse_price("19,99"), 19.99)
check("entero simple", parse_price("Precio: 49990 pesos"), 49990.0)
check("sin numero", parse_price("Agotado"), None)
check("cero invalido", parse_price("$0"), None)

JSONLD = """<html><head><script type="application/ld+json">
{"@type":"Product","offers":{"@type":"Offer","price":"89990","priceCurrency":"CLP"}}
</script></head><body></body></html>"""
check("json-ld", autodetect_price(BeautifulSoup(JSONLD, "lxml")), 89990.0)

META = '<html><head><meta property="og:price:amount" content="1.499.000"></head></html>'
check("og:price", autodetect_price(BeautifulSoup(META, "lxml")), 1499000.0)

# ---------- detector temporal ----------
det = Detector({"min_history": 5, "min_drop_pct": 35.0, "mad_threshold": 4.0})
stable = [{"price": p} for p in [100000, 101000, 99000, 100500, 100000, 99500, 100200, 100800]]

check("precio normal no alerta", det.evaluate(100300, stable).is_anomaly, False)
check("error de precio alerta", det.evaluate(9990, stable).is_anomaly, True)
check("descuento moderado no alerta", det.evaluate(75000, stable).is_anomaly, False)
check("historial corto no alerta", det.evaluate(5000, stable[:3]).is_anomaly, False)
check("subida no alerta", det.evaluate(200000, stable).is_anomaly, False)

flat = [{"price": 50000} for _ in range(10)]
check("MAD cero con caida grande", det.evaluate(1000, flat).is_anomaly, True)
check("MAD cero sin cambio", det.evaluate(50000, flat).is_anomaly, False)

# ---------- códigos de modelo ----------
check("codigo real", "fb3026la" in model_codes("VICTUS 15-FB3026LA RYZEN 7"), True)
check("gb no es modelo", model_codes("Notebook 16GB 512GB"), set())
check("ml no es modelo", model_codes("Perfume 100ML"), set())

VICTUS_A = "NOTEBOOK GAMER HP VICTUS 15-FB3026LA AMD RYZEN 7 16GB RAM 512GB SSD"
VICTUS_B = "NOTEBOOK HP GAMER VICTUS 15-FB3000LA AMD RYZEN 5 16GB RAM 512GB SSD"
HP_A = "Notebook 15-fd0274la Intel Core i7 16gb Ram 512gb Ssd"
HP_B = "NOTEBOOK HP 15-FD0274LA INTEL CORE I7 16GB RAM 512GB SSD"

check("modelos distintos no son el mismo",
      same_product(VICTUS_A, VICTUS_B, tokens(VICTUS_A), tokens(VICTUS_B), 0.6), False)
check("mismo modelo en dos tiendas",
      same_product(HP_A, HP_B, tokens(HP_A), tokens(HP_B), 0.6), True)

# ---------- detección sobre listados ----------
disc = Discovery(scraper=None, settings={"min_discount_pct": 80.0, "min_cross_store_pct": 45.0})

normal = Found("falabella", "Zapatilla Running", "u1", price=26990, reference_price=42990)
check("descuento retail normal no alerta", disc._detect_discounts([normal]), [])

error = Found("falabella", "Notebook X1 Pro", "u2", price=49990, reference_price=999990)
alerts = disc._detect_discounts([error])
check("descuento de 95% alerta", len(alerts), 1)
check("tipo de alerta", alerts[0].kind if alerts else None, "descuento")

# mismo modelo, una tienda mucho más barata
cross = [
    Found("falabella", HP_A, "a", price=200000),
    Found("ripley", HP_B, "b", price=690000),
    Found("ripley", HP_B + " X", "c", price=700000),
]
cross_alerts = disc._detect_cross_store(cross)
check("comparacion entre tiendas alerta", len(cross_alerts), 1)
check("elige el mas barato", cross_alerts[0].found.url if cross_alerts else None, "a")

# una sola tienda no basta para comparar
one_store = [
    Found("falabella", HP_A, "a", price=200000),
    Found("falabella", HP_B, "b", price=690000),
]
check("una sola tienda no compara", disc._detect_cross_store(one_store), [])

# ---------- adaptadores, con HTML de laboratorio ----------
FALABELLA_HTML = """<html><body><script id="__NEXT_DATA__" type="application/json">
{"props":{"pageProps":{"results":[
 {"displayName":"Notebook Prueba","url":"https://x.cl/p/1","brand":"ACME","productId":"1",
  "prices":[{"type":"normalPrice","crossed":true,"price":["1.000.000"]},
            {"type":"eventPrice","crossed":false,"price":["250.000"]}]},
 {"displayName":"Sin precio","url":"https://x.cl/p/2","prices":[]}
]}}}</script></body></html>"""

fal = Falabella().parse(FALABELLA_HTML)
check("falabella parsea 1 producto util", len(fal), 1)
check("falabella toma el precio no tachado", fal[0].price, 250000.0)
check("falabella toma la referencia tachada", fal[0].reference_price, 1000000.0)
check("falabella calcula descuento", round(fal[0].discount_pct), 75)

RIPLEY_HTML = """<html><body><script id="__NEXT_DATA__" type="application/json">
{"props":{"pageProps":{"findabilityProps":{"data":{"products":[
 {"name":"Tele Prueba","sku":"999","priceNumber":150000,"oldPrice":"$300.000","brand":"ACME"},
 {"name":"Sin sku","priceNumber":1000}
]}}}}}</script></body></html>"""

rip = Ripley().parse(RIPLEY_HTML)
check("ripley parsea 1 producto util", len(rip), 1)
check("ripley precio numerico", rip[0].price, 150000.0)
check("ripley precio anterior", rip[0].reference_price, 300000.0)
check("ripley arma url por sku", rip[0].url, "https://simple.ripley.cl/search/999")

# HTML inservible no debe reventar
check("html vacio no revienta", Falabella().parse("<html></html>"), [])
check("json roto no revienta",
      Ripley().parse('<script id="__NEXT_DATA__">{no es json}</script>'), [])

PARIS_HTML = """<html><body>
<div data-cnstrc-item-id="42" data-cnstrc-item-name="Notebook &amp; Mouse 15&quot;">
  <a href="/notebook-mouse-42">ficha</a>
  <span>28%</span><span>$249.990</span><span>$349.990</span>
</div>
<div data-cnstrc-item-id="43" data-cnstrc-item-name="Sin enlace"><span>$1.000</span></div>
</body></html>"""

par = Paris().parse(PARIS_HTML)
check("paris parsea 1 producto util", len(par), 1)
check("paris toma el precio menor", par[0].price, 249990.0)
check("paris toma el mayor como referencia", par[0].reference_price, 349990.0)
check("paris arma url absoluta", par[0].url, "https://www.paris.cl/notebook-mouse-42")

# El precio por unidad de medida NO es lo que se paga. Paris lo muestra junto al
# precio real; contarlo generaba alertas falsas por las dos puntas.
PARIS_ML = """<html><body>
<div data-cnstrc-item-id="7" data-cnstrc-item-name="Body Mist Cloud 236 ML">
  <a href="/body-mist-236">ficha</a>
  <span>54%</span><span>$9.990</span><span>(</span><span>$4.233 x 100 ml</span><span>)</span>
  <span>$21.990</span>
</div>
<div data-cnstrc-item-id="8" data-cnstrc-item-name="Perfume EDP 30 ml">
  <a href="/perfume-30">ficha</a>
  <span>51%</span><span>$36.990</span><span>$123.300 x 100 ml</span><span>$75.990</span>
</div>
<div data-cnstrc-item-id="9" data-cnstrc-item-name="Toalla 100 un">
  <a href="/toalla">ficha</a><span>$7.990</span><span>$7.990 x 100 un</span><span>$8.990</span>
</div>
</body></html>"""

ml = Paris().parse(PARIS_ML)
check("parsea los tres productos", len(ml), 3)
check("ignora el precio por mililitro barato", ml[0].price, 9990.0)
check("referencia correcta pese al precio unitario", ml[0].reference_price, 21990.0)
check("descuento realista, no del 81%", round(ml[0].discount_pct), 55)
# En envases pequeños el precio por 100 ml es MAYOR que el real: si se colara,
# se tomaría como "precio anterior" e inventaría un descuento enorme.
check("envase pequeño: precio correcto", ml[1].price, 36990.0)
check("envase pequeño: referencia sin inflar", ml[1].reference_price, 75990.0)
check("envase pequeño: descuento realista", round(ml[1].discount_pct), 51)
check("unidades 'un' tambien se ignoran", ml[2].price, 7990.0)
check("unidades 'un': referencia", ml[2].reference_price, 8990.0)

# Sodimac comparte plataforma con Falabella: mismo parser, distinto dominio.
check("sodimac usa el parser del grupo", len(Sodimac().parse(FALABELLA_HTML)), 1)
check("sodimac apunta a su dominio",
      Sodimac().listing_urls("taladro", None)[0].startswith("https://www.sodimac.cl"), True)

# ---------- mensaje de Telegram ----------
# Un nombre con & y comillas rompía el mensaje entero: Telegram rechazaba el
# HTML y la alerta se perdía sin avisar.
msg = format_alert('Taladro & Sierra 15" <Pro>', "https://x.cl/a?b=1&c=2",
                   "CLP", 1299990, 2499990, "descuento de 48% & pico")
check("escapa el ampersand del nombre", "&amp;" in msg, True)
check("no deja < sin escapar", "<Pro>" in msg, False)
check("conserva el formato de negrita", "<b>" in msg, True)
check("formatea miles con puntos", "1.299.990" in msg, True)

check("traduce token invalido",
      "token" in explain("Unauthorized").lower(), True)
check("traduce chat inexistente",
      "chat id" in explain("Bad Request: chat not found").lower(), True)

# ---------- la configuración no debe vivir en el repositorio ----------
# La configuración guarda la contraseña de la base en la nube. Si se escribiera
# dentro del proyecto, un `git add .` la publicaría.
from price_radar.config import CONFIG_PATH, ROOT, data_dir

check("los datos no se guardan en el proyecto", ROOT in data_dir().parents, False)
check("la configuración no está en el proyecto", str(CONFIG_PATH).startswith(str(ROOT)), False)

from price_radar.db import clean_url, is_postgres_url

BOM = "﻿"
check("limpia el BOM invisible", is_postgres_url(clean_url(BOM + "postgresql://a:b@c/d")), True)
check("limpia comillas", is_postgres_url(clean_url("'postgresql://a:b@c/d'")), True)
check("limpia saltos de linea", is_postgres_url(clean_url("postgresql://a:b@c/d\r\n")), True)
check("rechaza vacío", is_postgres_url(clean_url("")), False)
check("rechaza otro motor", is_postgres_url(clean_url("mysql://a:b@c/d")), False)

# ---------- respeto a robots.txt ----------
check("ripley no usa /search/ para descubrir",
      any("/search/" in u for u in Ripley().INTERNAL), False)

if failures:
    print("FALLOS:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("Todas las pruebas pasaron.")
