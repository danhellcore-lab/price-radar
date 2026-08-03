# Price Radar

Vigila precios en páginas web, guarda un histórico y alerta cuando detecta un
posible **error de precio**.

## Para usarlo

Doble clic en **`PriceRadar.exe`**, que está en tu Escritorio. No requiere
instalación ni tener Python. Todos los archivos `.py` de esta carpeta son el
código fuente; no necesitas abrir ninguno.

1. Pestaña **Categorías** → escribe algo como `notebook` → *Añadir y buscar*.
2. En menos de un minuto tienes ~100 productos vigilados de las dos tiendas.
3. Deja la app abierta. Vuelve a buscar sola cada 30 minutos (ajustable).

También puedes añadir una URL suelta desde *Productos encontrados → Añadir por URL*.

Sus datos (base de datos, ajustes, log) viven en `%APPDATA%\PriceRadar`.

### Recompilar el .exe

```bash
.venv\Scripts\python.exe build_exe.py
```

Genera el logo si falta, compila, y deja una copia lista en el Escritorio.
Para cambiar el logo, edita `make_icon.py` y vuelve a compilar.

### Que arranque con Windows

Pulsa `Win+R`, escribe `shell:startup` y arrastra un acceso directo de
`PriceRadar.exe` a esa carpeta.

## Cómo funciona por dentro

1. **Scraper** (`price_radar/scraper.py`) — descarga cada URL respetando
   `robots.txt`, con un delay por dominio y reintentos ante 429/503.
0. **Tiendas** (`price_radar/stores.py`) — adaptadores que convierten una
   categoría en una lista de productos con precio. Leen el JSON que las tiendas
   incrustan en el HTML (`__NEXT_DATA__`), no selectores CSS: el diseño de una
   página cambia seguido, la estructura de datos casi nunca. Una sola petición
   devuelve ~50 productos.
2. **Histórico** (`price_radar/storage.py`) — SQLite (`data/prices.db`) con
   productos, observaciones (incluidos los fallos) y alertas.
3. **Detección** — tres señales independientes:
   - `discovery.py` — **descuento declarado extremo**. Ver calibración abajo.
   - `discovery.py` — **comparación entre tiendas**: mismo modelo mucho más
     barato en una que en otra. Empareja por código de modelo (`fb3026la`), no
     por parecido de nombre, para no comparar un i5 contra un i7.
   - `detector.py` — **contra su propio histórico**, con **mediana + MAD** en
     vez de media + desviación estándar, porque los outliers que buscamos
     contaminarían un baseline basado en la media.

   Las dos primeras funcionan desde la primera búsqueda; la tercera necesita
   ~8 lecturas del mismo producto.
4. **Alerta** (`price_radar/alerts.py`) — mensaje a Telegram, con `cooldown_hours`
   para no repetir la misma alerta.
5. **Interfaz** — `price_radar/gui.py` (la app de escritorio, Tkinter) y
   `price_radar/web.py` (dashboard web opcional con sparklines, FastAPI en
   `127.0.0.1:8000`).

Categorías y productos se guardan en la base de datos y se gestionan desde la
app; `config.yaml` solo tiene ajustes.

## Por qué el umbral de descuento es 80%

No es un número inventado. Se midieron **609 descuentos reales** en 6 categorías
(notebook, zapatillas, televisor, audífonos, refrigerador, perfume) de ambas
tiendas:

| Percentil | Descuento |
|-----------|-----------|
| 50 (mediana) | 34,9% |
| 90 | 53,3% |
| 99 | 65,3% |
| máximo observado | 74,9% |

El retail chileno nunca pasó del 75%. Con umbral en 80% hay **cero falsos
positivos** sobre esa muestra, y cualquier cosa que lo supere es genuinamente
anómala. Reproducible con `main.py find <categoria>`.

## Tiendas soportadas

| Tienda | Vía | Nota |
|--------|-----|------|
| Falabella | Búsqueda (`/search`), JSON `__NEXT_DATA__` | Permitida por su robots.txt |
| Sodimac | Igual que Falabella | Misma plataforma; hereda el parser |
| Paris | Búsqueda, atributos `data-cnstrc-*` | robots.txt permite todo |
| Ripley | Páginas de categoría | Su robots.txt **prohíbe** `/search/`, así que se resuelve la categoría contra su sitemap |

Ninguna tienda cubre todas las categorías: Sodimac no aporta nada en
«zapatillas» y eso no es un error.

Descartadas tras probarlas: Lider, PC Factory, MercadoLibre, Hites, La Polar,
ABCDin, Tricot, Preunic y Easy (cargan precios con JavaScript, devuelven 404 en
sus rutas públicas o bloquean el acceso). SP Digital devuelve 403. La API de
catálogo de VTEX no está expuesta en ninguna de las probadas.

## Modo desarrollador / línea de comandos

```bash
cd price-radar && python -m venv .venv && .venv\Scripts\python.exe -m pip install -r requirements.txt
```

Buscar una categoría y ver el resultado sin guardar nada:

```bash
.venv\Scripts\python.exe main.py find notebook
```

Probar que un selector extrae bien el precio de una URL suelta:

```bash
.venv\Scripts\python.exe main.py test-url "https://tienda.cl/producto" ".price .amount"
```

Un ciclo único (útil para tareas programadas de Windows):

```bash
.venv\Scripts\python.exe main.py check
```

Dashboard + escaneo automático cada `interval_minutes`:

```bash
.venv\Scripts\python.exe main.py serve
```

Tests:

```bash
.venv\Scripts\python.exe tests\test_core.py
```

## Selectores CSS

Cuando la autodetección falla, hay que señalar dónde está el precio: clic
derecho sobre el precio → Inspeccionar → clic derecho en el nodo → Copy →
Copy selector. Simplifícalo a algo estable (`.product-price` en vez de
`div:nth-child(7) > span`), que sobreviva a un rediseño menor.

## Telegram

Desde la app: pestaña **Ajustes**.

1. Habla con [@BotFather](https://t.me/BotFather) → `/newbot` → copia el token.
2. **Escríbele cualquier mensaje a tu bot nuevo.** Sin este paso Telegram no
   permite que el bot inicie la conversación, y no hay chat que descubrir.
3. Pega el token en Ajustes y pulsa *Probar Telegram*: descubre tu chat ID
   solo, envía un mensaje de prueba y, si funciona, **activa y guarda** la
   configuración por ti.

Los mensajes de error de Telegram se traducen a algo accionable ("el token no
es válido", "escríbele un mensaje a tu bot") en vez de mostrar el inglés crudo
de la API.

Con Telegram desactivado las alertas igual se guardan en la base de datos y se
imprimen en el log.

## Ajustar la sensibilidad

- Demasiados falsos positivos → sube `min_drop_pct` y/o `mad_threshold`.
- Se te escapan errores reales → bájalos, pero espera más ruido en liquidaciones
  y Black Friday.
- `min_history: 8` significa que un producto nuevo no alertará hasta tener 8
  lecturas exitosas (4 horas con el intervalo de 30 min).

## Advertencias

- El scraping puede infringir los Términos de Servicio de algunas tiendas y
  provocar bloqueos de IP. Por defecto se respeta `robots.txt` y se espera 8s
  entre requests al mismo dominio; no bajes eso ni desactives `respect_robots`
  sin saber lo que haces.
- Sitios que renderizan el precio con JavaScript no funcionarán con este
  scraper (requiere HTML servido). Ahí hace falta un navegador headless.
- Los adaptadores dependen de la estructura interna de cada tienda. Si una
  cambia su web, ese adaptador deja de traer productos: se ve como «sin
  resultados» en la categoría. `tests/test_core.py` prueba el parseo con HTML
  de laboratorio, así que detecta regresiones de código, no cambios en la tienda.
- Un error de precio detectado no garantiza que la tienda respete la venta.
