"""Interfaz gráfica de Price Radar (Tkinter).

Diseñada para guiar al usuario: cada pantalla dice qué hacer a continuación y
los errores explican cómo arreglarlos en vez de mostrar una traza técnica.
"""
from __future__ import annotations

import logging
import queue
import threading
import webbrowser
from datetime import datetime, timezone
from tkinter import BOTH, END, LEFT, RIGHT, W, X, Tk, StringVar, BooleanVar
from tkinter import messagebox, ttk
import tkinter as tk

from .alerts import TelegramNotifier
from .config import Config, Target, data_dir, resource_path
from .engine import Engine
from .scraper import Scraper
from .storage import Storage
from .stores import ADAPTERS

log = logging.getLogger(__name__)

BG = "#f4f6f9"
CARD = "#ffffff"
ACCENT = "#2563eb"
DANGER = "#dc2626"
OK = "#059669"
MUTED = "#6b7280"


def human_time(iso: str | None) -> str:
    if not iso:
        return "nunca"
    ts = datetime.fromisoformat(iso)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - ts
    minutes = int(delta.total_seconds() // 60)
    if minutes < 1:
        return "recién"
    if minutes < 60:
        return f"hace {minutes} min"
    if minutes < 1440:
        return f"hace {minutes // 60} h"
    return ts.astimezone().strftime("%d-%m-%Y %H:%M")


def money(value: float | None, currency: str = "") -> str:
    if value is None:
        return "—"
    return f"{currency} {value:,.0f}".replace(",", ".").strip()


class AddProductDialog(tk.Toplevel):
    """Asistente de 'añadir producto' con prueba de extracción incorporada.

    No deja guardar hasta que se ha leído un precio real: así el usuario nunca
    añade un producto que en silencio nunca va a funcionar.
    """

    def __init__(self, parent: "PriceRadarApp"):
        super().__init__(parent.root)
        self.app = parent
        self.result: dict | None = None
        self.detected_price: float | None = None

        self.title("Añadir producto")
        self.configure(bg=BG, padx=24, pady=20)
        self.resizable(False, False)
        self.transient(parent.root)
        self.grab_set()

        ttk.Label(self, text="1. Pega la dirección del producto", style="Step.TLabel").pack(anchor=W)
        ttk.Label(
            self,
            text="Abre la página del producto en tu navegador y copia la URL de la barra de direcciones.",
            style="Hint.TLabel",
            wraplength=520,
        ).pack(anchor=W, pady=(0, 4))

        self.url_var = StringVar()
        ttk.Entry(self, textvariable=self.url_var, width=68).pack(fill=X, pady=(0, 14))

        ttk.Label(self, text="2. Ponle un nombre", style="Step.TLabel").pack(anchor=W)
        self.name_var = StringVar()
        ttk.Entry(self, textvariable=self.name_var, width=68).pack(fill=X, pady=(4, 14))

        ttk.Label(self, text="3. Comprueba que se lee el precio", style="Step.TLabel").pack(anchor=W)
        ttk.Label(
            self,
            text="Pulsa el botón y la app intentará leer el precio sola. Si no lo consigue, "
            "te pedirá un dato extra.",
            style="Hint.TLabel",
            wraplength=520,
        ).pack(anchor=W, pady=(0, 6))

        row = ttk.Frame(self)
        row.pack(fill=X)
        self.test_btn = ttk.Button(row, text="🔍  Probar ahora", command=self.on_test, style="Accent.TButton")
        self.test_btn.pack(side=LEFT)
        self.status = ttk.Label(row, text="", style="Hint.TLabel", wraplength=380)
        self.status.pack(side=LEFT, padx=12)

        # Campo avanzado, oculto hasta que la autodetección falla.
        self.adv = ttk.Frame(self)
        ttk.Label(
            self.adv,
            text="No pude encontrar el precio solo. Necesito que me señales dónde está:\n"
            "en el navegador, clic derecho sobre el precio → Inspeccionar → clic derecho en la\n"
            "línea marcada → Copy → Copy selector. Pega el resultado aquí.",
            style="Hint.TLabel",
            wraplength=520,
        ).pack(anchor=W, pady=(10, 4))
        self.selector_var = StringVar()
        ttk.Entry(self.adv, textvariable=self.selector_var, width=68).pack(fill=X)

        ttk.Separator(self, orient="horizontal").pack(fill=X, pady=16)

        buttons = ttk.Frame(self)
        buttons.pack(fill=X)
        ttk.Button(buttons, text="Cancelar", command=self.destroy).pack(side=RIGHT, padx=(8, 0))
        self.save_btn = ttk.Button(
            buttons, text="Guardar producto", command=self.on_save, style="Accent.TButton", state="disabled"
        )
        self.save_btn.pack(side=RIGHT)

        self.url_var.trace_add("write", lambda *_: self.invalidate())
        self.selector_var.trace_add("write", lambda *_: self.invalidate())

    def invalidate(self) -> None:
        """Cualquier cambio invalida la prueba anterior."""
        self.detected_price = None
        self.save_btn.state(["disabled"])

    def on_test(self) -> None:
        url = self.url_var.get().strip()
        if not url.startswith(("http://", "https://")):
            self.status.configure(text="La dirección debe empezar por http:// o https://", foreground=DANGER)
            return

        self.test_btn.state(["disabled"])
        self.status.configure(text="Consultando la página…", foreground=MUTED)

        selector = self.selector_var.get().strip() or None
        target = Target(name="test", url=url, price_selector=selector)

        def work() -> None:
            scraper = Scraper(self.app.config.scraper)
            result = scraper.fetch(target)
            self.app.root.after(0, lambda: self.on_test_done(result))

        threading.Thread(target=work, daemon=True).start()

    def on_test_done(self, result) -> None:
        self.test_btn.state(["!disabled"])
        if result.ok:
            self.detected_price = result.price
            self.status.configure(text=f"✓ Precio detectado: {money(result.price)}", foreground=OK)
            self.save_btn.state(["!disabled"])
            if not self.name_var.get().strip():
                self.name_var.set(self.url_var.get().strip().rstrip("/").split("/")[-1][:60])
            return

        self.status.configure(text=f"✗ {result.error}", foreground=DANGER)
        if not self.adv.winfo_ismapped():
            self.adv.pack(fill=X, after=self.status.master)

    def on_save(self) -> None:
        name = self.name_var.get().strip() or self.url_var.get().strip()[:60]
        self.result = {
            "url": self.url_var.get().strip(),
            "name": name,
            "price_selector": self.selector_var.get().strip() or None,
            "price": self.detected_price,
        }
        self.destroy()


class PriceRadarApp:
    def __init__(self) -> None:
        self.config = Config.load()
        configurada = (self.config.cloud.get("database_url") or "").strip()
        log.info("Configuración leída de: %s", self.config.path)
        log.info("Nube configurada: %s", "sí" if configurada else "NO")

        self.cloud_error = ""
        try:
            self.storage = Storage(url=configurada or None)
        except Exception as exc:
            # Si la nube no responde (Neon dormido, sin internet), se sigue
            # trabajando en local en vez de no abrir. Pero hay que DECIRLO:
            # antes esto se veía como "perdí todas mis categorías".
            log.exception("No se pudo abrir la base configurada")
            self.cloud_error = str(exc)
            self.storage = Storage()

        self.engine = Engine(self.config, self.storage)
        # En modo nube la búsqueda la hace GitHub Actions; aquí solo se mira.
        self.viewer_mode = self.storage.db.postgres
        log.info("Base en uso: %s (modo %s)", self.storage.location,
                 "nube" if self.viewer_mode else "local")

        self.scanning = False
        self.events: queue.Queue[tuple] = queue.Queue()
        # Resultado de la última comprobación manual de tiendas, si se hizo.
        self._store_states: dict[str, str] = {}

        self.root = Tk()
        self.root.title("Price Radar — cazador de errores de precio")
        self.root.geometry("980x660")
        self.root.minsize(820, 560)
        self.root.configure(bg=BG)
        self._set_window_icon()

        self._init_styles()
        self._build_header()
        self._build_tabs()
        self._build_statusbar()

        self.refresh()
        self.root.after(200, self._drain_events)
        self._schedule_auto_scan()

        if self.cloud_error:
            self.root.after(400, lambda: messagebox.showwarning(
                "No pude conectar con la nube",
                "Estás viendo la base local de este computador, no la de la nube.\n\n"
                f"Motivo: {self.cloud_error[:200]}\n\n"
                "Tus datos en la nube están intactos. Revisa tu conexión a internet "
                "y vuelve a abrir la aplicación.",
            ))

    # ---------- construcción de la interfaz ----------

    def _set_window_icon(self) -> None:
        icon = resource_path("icon.ico")
        if not icon.exists():
            return
        try:
            self.root.iconbitmap(default=str(icon))
        except tk.TclError:
            pass  # sin icono se sigue pudiendo usar la app

    def _init_styles(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(".", background=BG, font=("Segoe UI", 10))
        style.configure("TFrame", background=BG)
        style.configure("Card.TFrame", background=CARD, relief="flat")
        style.configure("TLabel", background=BG)
        style.configure("Title.TLabel", font=("Segoe UI", 16, "bold"))
        style.configure("Step.TLabel", font=("Segoe UI", 10, "bold"))
        style.configure("Hint.TLabel", foreground=MUTED)
        style.configure("Big.TLabel", font=("Segoe UI", 11, "bold"))
        style.configure("Accent.TButton", font=("Segoe UI", 10, "bold"))
        style.configure("Treeview", rowheight=30, fieldbackground=CARD, background=CARD)
        style.configure("Treeview.Heading", font=("Segoe UI", 9, "bold"))

    def _build_header(self) -> None:
        head = ttk.Frame(self.root, padding=(18, 14, 18, 8))
        head.pack(fill=X)
        ttk.Label(head, text="📡  Price Radar", style="Title.TLabel").pack(side=LEFT)

        if self.viewer_mode:
            ttk.Label(head, text="☁ conectado a la nube", style="Hint.TLabel").pack(
                side=LEFT, padx=12
            )
            self.scan_btn = ttk.Button(head, text="🔄  Actualizar", command=self.refresh)
            self.scan_btn.pack(side=RIGHT)
            if self.config.cloud.get("report_url"):
                ttk.Button(
                    head, text="🌐  Ver en la web",
                    command=lambda: webbrowser.open(self.config.cloud["report_url"]),
                ).pack(side=RIGHT, padx=6)
        else:
            self.scan_btn = ttk.Button(
                head, text="🔎  Buscar ahora", command=lambda: self.start_scan(),
                style="Accent.TButton",
            )
            self.scan_btn.pack(side=RIGHT)

        self.progress = ttk.Progressbar(head, mode="determinate", length=180)

    def _build_tabs(self) -> None:
        self.tabs = ttk.Notebook(self.root)
        self.tabs.pack(fill=BOTH, expand=True, padx=14, pady=6)

        self.tab_categories = ttk.Frame(self.tabs, padding=12)
        self.tab_products = ttk.Frame(self.tabs, padding=12)
        self.tab_alerts = ttk.Frame(self.tabs, padding=12)
        self.tab_stores = ttk.Frame(self.tabs, padding=12)
        self.tab_settings = ttk.Frame(self.tabs, padding=12)
        self.tab_help = ttk.Frame(self.tabs, padding=12)
        self.tabs.add(self.tab_categories, text="  Categorías  ")
        self.tabs.add(self.tab_alerts, text="  Alertas  ")
        self.tabs.add(self.tab_products, text="  Productos encontrados  ")
        self.tabs.add(self.tab_stores, text="  Páginas vigiladas  ")
        self.tabs.add(self.tab_settings, text="  Ajustes  ")
        self.tabs.add(self.tab_help, text="  Ayuda  ")

        self._build_categories_tab()
        self._build_stores_tab()
        self._build_products_tab()
        self._build_alerts_tab()
        self._build_settings_tab()
        self._build_help_tab()

    def _build_categories_tab(self) -> None:
        frm = self.tab_categories

        ttk.Label(
            frm,
            text="Dime qué tipo de producto te interesa y yo busco solo en las tiendas.",
            style="Big.TLabel",
        ).pack(anchor=W)
        ttk.Label(
            frm,
            text="Escribe una categoría como la buscarías en una tienda: «notebook», "
            "«zapatillas», «televisor», «perfume».",
            style="Hint.TLabel",
        ).pack(anchor=W, pady=(2, 12))

        row = ttk.Frame(frm)
        row.pack(fill=X)
        self.category_var = StringVar()
        entry = ttk.Entry(row, textvariable=self.category_var, width=34, font=("Segoe UI", 11))
        entry.pack(side=LEFT)
        entry.bind("<Return>", lambda _e: self.add_category())
        ttk.Button(row, text="➕  Añadir y buscar", command=self.add_category,
                   style="Accent.TButton").pack(side=LEFT, padx=8)
        ttk.Button(row, text="Quitar categoría", command=self.delete_category).pack(side=LEFT)

        chips = ttk.Frame(frm)
        chips.pack(fill=X, pady=(10, 4))
        ttk.Label(chips, text="Sugerencias:", style="Hint.TLabel").pack(side=LEFT)
        for suggestion in ("notebook", "celular", "televisor", "zapatillas",
                           "audifonos", "refrigerador", "perfume"):
            ttk.Button(
                chips, text=suggestion, width=len(suggestion) + 2,
                command=lambda s=suggestion: self.category_var.set(s),
            ).pack(side=LEFT, padx=2)

        columns = ("productos", "tiendas")
        self.cat_tree = ttk.Treeview(frm, columns=columns, show="tree headings", selectmode="browse")
        self.cat_tree.heading("#0", text="Categoría")
        self.cat_tree.column("#0", width=320, anchor=W)
        self.cat_tree.heading("productos", text="Productos vigilados")
        self.cat_tree.column("productos", width=160, anchor=W)
        self.cat_tree.heading("tiendas", text="Tiendas")
        self.cat_tree.column("tiendas", width=200, anchor=W)
        self.cat_tree.pack(fill=BOTH, expand=True, pady=(12, 0))

        self.cat_empty = ttk.Label(
            frm,
            text="Aún no has añadido ninguna categoría.\n"
            "Escribe una arriba (o pulsa una sugerencia) y dale a «Añadir y buscar».",
            style="Hint.TLabel",
            justify="center",
        )

    def _build_stores_tab(self) -> None:
        frm = self.tab_stores

        ttk.Label(frm, text="Estas son las páginas que el radar consulta",
                  style="Big.TLabel").pack(anchor=W)
        ttk.Label(
            frm,
            text="Se eligieron porque publican sus listados de forma que se pueden leer "
            "con fiabilidad y porque su robots.txt lo permite. Ninguna cubre todas las "
            "categorías: que una traiga 0 productos en «zapatillas» es normal.",
            style="Hint.TLabel",
            wraplength=780,
            justify=LEFT,
        ).pack(anchor=W, pady=(2, 10))

        bar = ttk.Frame(frm)
        bar.pack(fill=X, pady=(0, 8))
        self.check_stores_btn = ttk.Button(
            bar, text="🔌  Comprobar si responden", command=self.check_stores,
            style="Accent.TButton",
        )
        self.check_stores_btn.pack(side=LEFT)
        ttk.Button(bar, text="Abrir la página", command=self.open_store).pack(side=LEFT, padx=6)
        self.stores_status = ttk.Label(bar, text="", style="Hint.TLabel")
        self.stores_status.pack(side=LEFT, padx=10)

        columns = ("sitio", "acceso", "productos", "ultima", "estado")
        self.stores_tree = ttk.Treeview(frm, columns=columns, show="tree headings",
                                        selectmode="browse")
        self.stores_tree.heading("#0", text="Tienda")
        self.stores_tree.column("#0", width=110, anchor=W)
        for col, label, width in (
            ("sitio", "Dirección", 150),
            ("acceso", "Cómo la consulta", 230),
            ("productos", "Productos vigilados", 130),
            ("ultima", "Última lectura", 120),
            ("estado", "Estado", 90),
        ):
            self.stores_tree.heading(col, text=label)
            self.stores_tree.column(col, width=width, anchor=W)
        self.stores_tree.pack(fill=BOTH, expand=True)
        self.stores_tree.tag_configure("aviso", foreground=DANGER)

        self.stores_note = ttk.Label(frm, text="", style="Hint.TLabel", wraplength=780,
                                     justify=LEFT)
        self.stores_note.pack(anchor=W, pady=(10, 0))

        ttk.Label(
            frm,
            text="Probadas y descartadas: Lider, PC Factory, MercadoLibre, Hites, La Polar, "
            "ABCDin, Tricot, Preunic, Easy y SP Digital. Cargan los precios con JavaScript "
            "o bloquean el acceso, así que no se pueden leer de esta forma.",
            style="Hint.TLabel",
            wraplength=780,
            justify=LEFT,
        ).pack(anchor=W, pady=(8, 0))

    def _build_products_tab(self) -> None:
        bar = ttk.Frame(self.tab_products)
        bar.pack(fill=X, pady=(0, 10))
        ttk.Label(
            bar,
            text="Todo lo que encontró el radar. Se revisa solo cada cierto tiempo.",
            style="Hint.TLabel",
        ).pack(side=LEFT)
        ttk.Button(bar, text="Abrir en el navegador", command=self.open_selected).pack(side=RIGHT, padx=6)
        ttk.Button(bar, text="➕ Añadir por URL", command=self.add_product).pack(side=RIGHT)
        ttk.Button(bar, text="Eliminar", command=self.delete_selected).pack(side=RIGHT, padx=6)

        columns = ("tienda", "estado", "precio", "habitual", "lecturas", "ultima")
        self.tree = ttk.Treeview(self.tab_products, columns=columns, show="tree headings", selectmode="browse")
        self.tree.heading("#0", text="Producto")
        self.tree.column("#0", width=300, anchor=W)
        for col, label, width in (
            ("tienda", "Tienda", 90),
            ("estado", "Estado", 105),
            ("precio", "Último precio", 110),
            ("habitual", "Precio habitual", 115),
            ("lecturas", "Lecturas", 70),
            ("ultima", "Última revisión", 120),
        ):
            self.tree.heading(col, text=label)
            self.tree.column(col, width=width, anchor=W)
        self.tree.pack(fill=BOTH, expand=True)
        self.tree.tag_configure("error", foreground=DANGER)
        self.tree.tag_configure("pausado", foreground=MUTED)

        self.empty_hint = ttk.Label(
            self.tab_products,
            text="Todavía no hay productos.\n"
            "Ve a la pestaña «Categorías» y añade una: el radar los encuentra solo.",
            style="Hint.TLabel",
            justify="center",
        )

    def _build_alerts_tab(self) -> None:
        ttk.Label(
            self.tab_alerts,
            text="Aquí aparecen los precios que se salen de lo normal. "
            "Haz doble clic en una alerta para abrir la página.",
            style="Hint.TLabel",
        ).pack(anchor=W, pady=(0, 10))

        columns = ("precio", "habitual", "caida", "cuando")
        self.alerts_tree = ttk.Treeview(self.tab_alerts, columns=columns, show="tree headings", selectmode="browse")
        self.alerts_tree.heading("#0", text="Producto")
        self.alerts_tree.column("#0", width=320, anchor=W)
        for col, label, width in (
            ("precio", "Precio visto", 120),
            ("habitual", "Precio habitual", 130),
            ("caida", "Caída", 90),
            ("cuando", "Cuándo", 150),
        ):
            self.alerts_tree.heading(col, text=label)
            self.alerts_tree.column(col, width=width, anchor=W)
        self.alerts_tree.pack(fill=BOTH, expand=True)
        self.alerts_tree.bind("<Double-1>", lambda _e: self.open_alert())

    def _build_settings_tab(self) -> None:
        frm = self.tab_settings

        ttk.Label(frm, text="Cada cuánto revisar", style="Step.TLabel").pack(anchor=W)
        ttk.Label(
            frm,
            text="Revisar muy seguido no sirve de mucho y aumenta el riesgo de que la tienda te bloquee.",
            style="Hint.TLabel",
        ).pack(anchor=W)
        self.interval_var = StringVar(value=str(self.config.scheduler.get("interval_minutes", 30)))
        row = ttk.Frame(frm)
        row.pack(anchor=W, pady=(4, 16))
        ttk.Combobox(
            row, textvariable=self.interval_var, width=8, state="readonly",
            values=("15", "30", "60", "120", "360"),
        ).pack(side=LEFT)
        ttk.Label(row, text="minutos", style="Hint.TLabel").pack(side=LEFT, padx=6)

        ttk.Label(frm, text="Cuándo avisar", style="Step.TLabel").pack(anchor=W)
        ttk.Label(
            frm,
            text="Un producto solo genera alerta si su precio baja al menos este porcentaje "
            "respecto a lo que ha costado habitualmente.",
            style="Hint.TLabel",
            wraplength=700,
        ).pack(anchor=W)
        self.drop_var = StringVar(value=str(int(self.config.detector.get("min_drop_pct", 35))))
        row2 = ttk.Frame(frm)
        row2.pack(anchor=W, pady=(4, 16))
        ttk.Combobox(
            row2, textvariable=self.drop_var, width=8, state="readonly",
            values=("25", "35", "50", "60", "75"),
        ).pack(side=LEFT)
        ttk.Label(row2, text="% de caída (más alto = menos avisos falsos)", style="Hint.TLabel").pack(side=LEFT, padx=6)

        ttk.Separator(frm, orient="horizontal").pack(fill=X, pady=8)

        ttk.Label(frm, text="Avisos al celular por Telegram (opcional)", style="Step.TLabel").pack(anchor=W)
        ttk.Label(
            frm,
            text="1. En Telegram busca @BotFather y envíale /newbot. Te dará un token.\n"
            "2. IMPORTANTE: abre el chat con tu bot nuevo y escríbele cualquier mensaje.\n"
            "    Sin ese paso Telegram no deja que el bot te escriba a ti.\n"
            "3. Pega el token aquí abajo y pulsa «Probar Telegram»: la app encuentra tu\n"
            "    chat sola, y si funciona lo activa y lo guarda por ti.",
            style="Hint.TLabel",
            justify=LEFT,
        ).pack(anchor=W, pady=(2, 6))

        tg = self.config.alerts.get("telegram", {})
        self.tg_enabled = BooleanVar(value=bool(tg.get("enabled")))
        self.tg_token = StringVar(value=tg.get("bot_token", ""))
        self.tg_chat = StringVar(value=tg.get("chat_id", ""))

        grid = ttk.Frame(frm)
        grid.pack(anchor=W, fill=X)
        ttk.Label(grid, text="Token del bot:").grid(row=0, column=0, sticky=W, pady=3)
        ttk.Entry(grid, textvariable=self.tg_token, width=52).grid(row=0, column=1, sticky=W, padx=8)
        ttk.Label(grid, text="Chat ID:").grid(row=1, column=0, sticky=W, pady=3)
        ttk.Entry(grid, textvariable=self.tg_chat, width=24).grid(row=1, column=1, sticky=W, padx=8)
        ttk.Checkbutton(grid, text="Enviar avisos por Telegram", variable=self.tg_enabled).grid(
            row=2, column=1, sticky=W, padx=8, pady=(6, 0)
        )

        row3 = ttk.Frame(frm)
        row3.pack(anchor=W, pady=14)
        ttk.Button(row3, text="Probar Telegram", command=self.test_telegram).pack(side=LEFT)
        ttk.Button(row3, text="💾  Guardar ajustes", command=self.save_settings, style="Accent.TButton").pack(
            side=LEFT, padx=8
        )
        self.settings_status = ttk.Label(row3, text="", style="Hint.TLabel")
        self.settings_status.pack(side=LEFT, padx=10)

        ttk.Separator(frm, orient="horizontal").pack(fill=X, pady=12)

        ttk.Label(frm, text="Funcionar en la nube (sin tu computador encendido)",
                  style="Step.TLabel").pack(anchor=W)
        ttk.Label(
            frm,
            text="Pega aquí la cadena de conexión de Neon y esta app pasará a ser un visor: "
            "la búsqueda la hará GitHub Actions cada 30 minutos, aunque apagues el PC.\n"
            "Los pasos completos están en el README, sección «Funcionar en la nube».",
            style="Hint.TLabel",
            justify=LEFT,
            wraplength=720,
        ).pack(anchor=W, pady=(2, 6))

        cloud = ttk.Frame(frm)
        cloud.pack(anchor=W, fill=X)
        self.cloud_url = StringVar(value=self.config.cloud.get("database_url", ""))
        self.report_url = StringVar(value=self.config.cloud.get("report_url", ""))
        ttk.Label(cloud, text="Conexión Neon:").grid(row=0, column=0, sticky=W, pady=3)
        ttk.Entry(cloud, textvariable=self.cloud_url, width=62, show="•").grid(
            row=0, column=1, sticky=W, padx=8)
        ttk.Label(cloud, text="URL del informe web:").grid(row=1, column=0, sticky=W, pady=3)
        ttk.Entry(cloud, textvariable=self.report_url, width=62).grid(
            row=1, column=1, sticky=W, padx=8)

        estado = "☁ Conectado a la nube" if self.viewer_mode else "💾 Usando la base local"
        ttk.Label(frm, text=f"{estado} — {self.storage.location}", style="Hint.TLabel").pack(
            anchor=W, pady=(10, 0))
        ttk.Label(
            frm, text=f"Los ajustes se guardan en: {data_dir()}", style="Hint.TLabel"
        ).pack(anchor=W, pady=(2, 0))

    def _build_help_tab(self) -> None:
        text = tk.Text(
            self.tab_help, wrap="word", relief="flat", bg=CARD, font=("Segoe UI", 10),
            padx=16, pady=14, height=20,
        )
        text.pack(fill=BOTH, expand=True)
        text.insert(
            END,
            "¿Qué hace esta aplicación?\n\n"
            "Tú le das categorías de productos. Ella busca sola en las tiendas, guarda "
            "todos los precios que encuentra y te avisa cuando alguno está anormalmente "
            "bajo — que es como se ven los errores de precio.\n\n"
            "Cómo empezar\n\n"
            "1. Ve a «Categorías», escribe algo como «notebook» y pulsa «Añadir y buscar».\n"
            "2. En menos de un minuto tendrás unos 100 productos vigilados.\n"
            "3. Deja la aplicación abierta. Vuelve a mirar sola cada cierto tiempo.\n\n"
            "Cómo decide que un precio está mal\n\n"
            "Usa tres señales distintas:\n\n"
            "• Descuento imposible. Las tiendas publican su precio normal tachado. "
            "Midiendo 609 descuentos reales, el retail chileno tiene una mediana del 35% "
            "y nunca pasa del 75%. Si algo aparece con 80% o más de descuento, no es "
            "una promoción.\n\n"
            "• Comparación entre tiendas. Si el mismo modelo exacto está mucho más barato "
            "en una tienda que en la otra, salta el aviso. Compara códigos de modelo, "
            "no nombres parecidos, para no confundir un i5 con un i7.\n\n"
            "• Su propia historia. Con el tiempo aprende cuánto suele costar cada "
            "producto y avisa si se desploma respecto a sí mismo.\n\n"
            "Las dos primeras funcionan desde la primera búsqueda. La tercera necesita "
            "que pasen unas horas.\n\n"
            "Tiendas que revisa\n\n"
            "Falabella, Paris, Ripley y Sodimac. Se eligieron porque publican sus "
            "listados en un formato que se puede leer de forma fiable y porque su "
            "robots.txt lo permite. No todas cubren todo: si buscas «zapatillas», "
            "Sodimac no aporta nada, y es normal.\n\n"
            "Otras tiendas grandes (Lider, PC Factory, MercadoLibre, Hites, La Polar) "
            "cargan los precios con JavaScript o bloquean el acceso, y no se pueden "
            "leer de esta forma.\n\n"
            "Cosas que conviene saber\n\n"
            "• Que se detecte un error de precio no obliga a la tienda a vendértelo a ese precio.\n"
            "• La app espera unos segundos entre consultas y respeta el robots.txt de cada "
            "sitio. Una búsqueda por categoría trae ~50 productos de una sola petición, así "
            "que molesta muy poco a los servidores.\n"
            "• Revisar precios automáticamente puede ir contra los términos de uso de "
            "algunos sitios. Úsala con cabeza.\n",
        )
        text.configure(state="disabled")

    def _build_statusbar(self) -> None:
        bar = ttk.Frame(self.root, padding=(18, 6, 18, 10))
        bar.pack(fill=X)
        self.status_var = StringVar(value="Listo.")
        ttk.Label(bar, textvariable=self.status_var, style="Hint.TLabel").pack(side=LEFT)
        self.next_scan_var = StringVar(value="")
        ttk.Label(bar, textvariable=self.next_scan_var, style="Hint.TLabel").pack(side=RIGHT)

    # ---------- datos ----------

    def refresh(self) -> None:
        products = self.storage.products()

        # --- categorías ---
        self.cat_tree.delete(*self.cat_tree.get_children())
        categories = self.storage.categories()
        if categories:
            self.cat_empty.pack_forget()
        else:
            self.cat_empty.pack(pady=24)

        for c in categories:
            in_cat = [p for p in products if p["category"] == c["query"]]
            stores = sorted({p["store"] for p in in_cat if p["store"]})
            label = ", ".join(s.capitalize() for s in stores) if stores else "—"
            self.cat_tree.insert(
                "", END, iid=str(c["id"]), text=c["query"],
                values=(len(in_cat) or "buscando…", label),
            )

        # --- páginas vigiladas ---
        self._refresh_stores(products)

        # --- productos ---
        self.tree.delete(*self.tree.get_children())
        if not products:
            self.empty_hint.pack(pady=30)
        else:
            self.empty_hint.pack_forget()

        # Dos consultas para toda la tabla, no dos por producto: contra la base
        # en la nube, lo segundo tardaba minutos en dibujar la pantalla.
        latest = self.storage.latest_observations()
        stats = self.storage.price_stats(self.config.detector.get("window_days", 30))

        for p in products:
            last = latest.get(int(p["id"]))
            summary = stats.get(int(p["id"]))
            count = int(summary["n"]) if summary else 0
            lo, hi = (summary["lo"], summary["hi"]) if summary else (None, None)
            baseline = (lo + hi) / 2 if lo is not None and hi is not None else None

            tags: tuple[str, ...] = ()
            if not p["enabled"]:
                estado = "⏸ Pausado"
                tags = ("pausado",)
            elif last and not last["ok"]:
                estado = "⚠ Error"
                tags = ("error",)
            elif count < self.config.detector.get("min_history", 8):
                estado = "📊 Aprendiendo"
            else:
                estado = "✓ Vigilando"

            self.tree.insert(
                "", END, iid=str(p["id"]), text=p["name"],
                values=(
                    (p["store"] or "manual").capitalize(),
                    estado,
                    money(last["price"] if last else None, p["currency"]),
                    money(baseline, p["currency"]),
                    count,
                    human_time(last["ts"] if last else None),
                ),
                tags=tags,
            )

        self.alerts_tree.delete(*self.alerts_tree.get_children())
        for a in self.storage.recent_alerts(100):
            self.alerts_tree.insert(
                "", END, iid=str(a["id"]), text=a["name"],
                values=(
                    money(a["price"], a["currency"]),
                    money(a["baseline"], a["currency"]),
                    f"-{a['drop_pct']:.0f}%",
                    human_time(a["ts"]),
                ),
            )

    # ---------- páginas vigiladas ----------

    def _refresh_stores(self, products: list[dict]) -> None:
        self.stores_tree.delete(*self.stores_tree.get_children())
        latest = self.storage.latest_observations()

        # Última lectura por tienda, en una pasada.
        ultima: dict[str, str] = {}
        for p in products:
            obs = latest.get(int(p["id"]))
            if not obs:
                continue
            tienda = p["store"]
            if tienda and (tienda not in ultima or obs["ts"] > ultima[tienda]):
                ultima[tienda] = obs["ts"]

        notas = []
        for adapter in ADAPTERS:
            cuantos = sum(1 for p in products if p["store"] == adapter.name)
            estado = self._store_states.get(adapter.name)
            if estado is None:
                estado = "✓ Activa" if cuantos else "· Sin datos"
            tags = ("aviso",) if estado.startswith("✗") else ()

            self.stores_tree.insert(
                "", END, iid=adapter.name, text=adapter.label,
                values=(adapter.site, adapter.how, cuantos or "—",
                        human_time(ultima.get(adapter.name)), estado),
                tags=tags,
            )
            if adapter.note:
                notas.append(f"• {adapter.label}: {adapter.note}")

        if self.viewer_mode:
            notas.append(
                "• La búsqueda la hace un servidor de GitHub, no tu PC. Las tiendas que "
                "bloquean centros de datos no aportan productos a la nube."
            )
        self.stores_note.configure(text="\n".join(notas))

    def check_stores(self) -> None:
        """Pide una página real a cada tienda y reporta si responde."""
        self.check_stores_btn.state(["disabled"])
        self.stores_status.configure(text="Comprobando…", foreground=MUTED)

        def work() -> None:
            resultados: dict[str, str] = {}
            for adapter in ADAPTERS:
                try:
                    urls = adapter.listing_urls("notebook", self.engine.scraper)
                    if not urls:
                        resultados[adapter.name] = "· Sin categoría"
                        continue
                    html = self.engine.scraper.get_html(urls[0])
                    encontrados = len(adapter.parse(html))
                    resultados[adapter.name] = (
                        f"✓ Responde" if encontrados else "· Responde, 0 aquí"
                    )
                except Exception as exc:
                    detalle = str(exc)
                    if "403" in detalle:
                        resultados[adapter.name] = "✗ Bloqueada"
                    elif "robots" in detalle.lower():
                        resultados[adapter.name] = "✗ robots.txt"
                    else:
                        resultados[adapter.name] = "✗ Sin respuesta"
            self.events.put(("stores", resultados))

        threading.Thread(target=work, daemon=True).start()

    def open_store(self) -> None:
        sel = self.stores_tree.selection()
        if not sel:
            messagebox.showinfo("Price Radar", "Selecciona una tienda de la lista.")
            return
        for adapter in ADAPTERS:
            if adapter.name == sel[0]:
                webbrowser.open(f"https://{adapter.site}")
                return

    # ---------- acciones de categorías ----------

    def add_category(self) -> None:
        query = self.category_var.get().strip()
        if len(query) < 3:
            messagebox.showinfo(
                "Price Radar",
                "Escribe una categoría de al menos 3 letras.\n\n"
                "Por ejemplo: notebook, zapatillas, televisor.",
            )
            return
        if self.scanning:
            messagebox.showinfo("Price Radar", "Espera a que termine la búsqueda en curso.")
            return

        self.storage.add_category(query)
        self.category_var.set("")
        self.refresh()

        if self.viewer_mode:
            messagebox.showinfo(
                "Categoría añadida",
                f"«{query}» quedó guardada en la nube.\n\n"
                "La próxima ejecución automática (cada 30 minutos) la buscará. "
                "No hace falta que dejes esto abierto.",
            )
            return
        self.start_scan(only_category=query.lower())

    def delete_category(self) -> None:
        sel = self.cat_tree.selection()
        if not sel:
            messagebox.showinfo("Price Radar", "Selecciona una categoría de la lista.")
            return
        query = self.cat_tree.item(sel[0], "text")
        if messagebox.askyesno(
            "Quitar categoría",
            f"¿Dejar de vigilar «{query}»?\n\n"
            "Se eliminarán también los productos que encontró y su historial.",
        ):
            self.storage.delete_category(int(sel[0]))
            self.refresh()

    # ---------- acciones de productos ----------

    def add_product(self) -> None:
        dialog = AddProductDialog(self)
        self.root.wait_window(dialog)
        if not dialog.result:
            return
        r = dialog.result
        product_id = self.storage.upsert_product(
            r["url"], r["name"], price_selector=r["price_selector"]
        )
        # La lectura de la prueba ya es un dato válido: la guardamos para no
        # desperdiciar el request y adelantar el historial.
        if r["price"] is not None:
            self.storage.record_observation(product_id, r["price"], ok=True)
        self.refresh()
        self.status_var.set(f"Añadido: {r['name']}")

    def _selected_id(self) -> int | None:
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("Price Radar", "Primero selecciona un producto de la lista.")
            return None
        return int(sel[0])

    def open_selected(self) -> None:
        pid = self._selected_id()
        if pid is None:
            return
        product = self.storage.product(pid)
        if product:
            webbrowser.open(product["url"])

    def open_alert(self) -> None:
        sel = self.alerts_tree.selection()
        if not sel:
            return
        for a in self.storage.recent_alerts(100):
            if str(a["id"]) == sel[0]:
                webbrowser.open(a["url"])
                return

    def toggle_selected(self) -> None:
        pid = self._selected_id()
        if pid is None:
            return
        product = self.storage.product(pid)
        if product:
            self.storage.set_enabled(pid, not product["enabled"])
            self.refresh()

    def delete_selected(self) -> None:
        pid = self._selected_id()
        if pid is None:
            return
        product = self.storage.product(pid)
        if not product:
            return
        if messagebox.askyesno(
            "Eliminar producto",
            f"¿Eliminar «{product['name']}»?\n\nSe borrará también todo su historial de precios.",
        ):
            self.storage.delete_product(pid)
            self.refresh()

    # ---------- escaneo ----------

    def start_scan(self, only_category: str | None = None) -> None:
        if self.scanning:
            return
        if not self.storage.categories() and not self.storage.products():
            messagebox.showinfo(
                "Price Radar",
                "Todavía no hay nada que revisar.\n\n"
                "Ve a «Categorías», escribe algo como «notebook» y pulsa «Añadir y buscar».",
            )
            return

        self.scanning = True
        self.scan_btn.state(["disabled"])
        self.progress.pack(side=RIGHT, padx=12)
        self.progress.configure(mode="indeterminate")
        self.progress.start(12)

        def say(message: str) -> None:
            self.events.put(("status", message))

        def work() -> None:
            try:
                if only_category:
                    result = self.engine.scan_category(only_category, progress=say)
                    stats = {
                        "found": result["found"], "alerts": result["alerts"],
                        "errors": result["errors"], "categories": 1,
                    }
                else:
                    stats = self.engine.scan_all_categories(progress=say)
                    if self.engine.targets():  # productos añadidos a mano por URL
                        say("Revisando los productos añadidos a mano…")
                        extra = self.engine.run_cycle()
                        stats["alerts"] += extra["alerts"]
                self.events.put(("done", stats))
            except Exception as exc:  # la interfaz nunca debe morir por un fallo de red
                self.events.put(("error", exc))

        threading.Thread(target=work, daemon=True).start()

    def _drain_events(self) -> None:
        try:
            while True:
                event = self.events.get_nowait()
                kind = event[0]
                if kind == "status":
                    self.status_var.set(event[1])
                elif kind == "stores":
                    self._store_states = event[1]
                    self.check_stores_btn.state(["!disabled"])
                    caidas = sum(1 for v in event[1].values() if v.startswith("✗"))
                    self.stores_status.configure(
                        text="Todas responden" if not caidas
                        else f"{caidas} tienda(s) no responden desde aquí",
                        foreground=OK if not caidas else DANGER,
                    )
                    self.refresh()
                elif kind == "done":
                    self._scan_finished(event[1])
                elif kind == "error":
                    self._scan_finished(None, error=event[1])
        except queue.Empty:
            pass
        self.root.after(200, self._drain_events)

    def _scan_finished(self, stats: dict | None, error: Exception | None = None) -> None:
        self.scanning = False
        self.scan_btn.state(["!disabled"])
        self.progress.stop()
        self.progress.pack_forget()
        self.refresh()

        if error is not None:
            self.status_var.set(f"La búsqueda falló: {error}")
            return

        found = stats.get("found", 0)
        alerts = stats.get("alerts", 0)
        errors = stats.get("errors") or []

        parts = [f"{found} productos revisados"]
        if alerts:
            parts.append(f"⚠ {alerts} alerta(s)")
        if errors:
            parts.append(f"{len(errors)} aviso(s) de tienda")
        self.status_var.set("Búsqueda terminada — " + ", ".join(parts))

        if alerts:
            self.tabs.select(self.tab_alerts)
            messagebox.showwarning(
                "¡Posible error de precio!",
                f"Se detectaron {alerts} precio(s) anormalmente bajos.\n\n"
                "Mira la pestaña «Alertas».",
            )
        elif found == 0 and errors:
            messagebox.showwarning(
                "Sin resultados",
                "No encontré productos para esa categoría.\n\n"
                + "\n".join(f"• {e}" for e in errors[:4])
                + "\n\nPrueba con una palabra más común, como «notebook» o «zapatillas».",
            )

    # ---------- programación automática ----------

    def _schedule_auto_scan(self) -> None:
        minutes = int(self.config.scheduler.get("interval_minutes", 30))
        self._next_scan_at = datetime.now(timezone.utc).timestamp() + minutes * 60
        self._tick()

    def _tick(self) -> None:
        if self.viewer_mode:
            # Buscar es tarea de la nube; aquí solo se refresca la vista.
            self.next_scan_var.set("La búsqueda corre en la nube cada 30 min")
            self.root.after(120_000, self._refresh_quietly)
            return

        remaining = int(self._next_scan_at - datetime.now(timezone.utc).timestamp())
        if remaining <= 0:
            if not self.scanning and (self.storage.categories() or self.storage.products()):
                self.start_scan()
            minutes = int(self.config.scheduler.get("interval_minutes", 30))
            self._next_scan_at = datetime.now(timezone.utc).timestamp() + minutes * 60
        else:
            self.next_scan_var.set(f"Próxima revisión automática en {remaining // 60 + 1} min")
        self.root.after(1000, self._tick)

    def _refresh_quietly(self) -> None:
        """Relee la nube de vez en cuando, sin molestar si falla la red."""
        try:
            self.refresh()
            self.status_var.set(f"Actualizado desde la nube · {datetime.now().strftime('%H:%M')}")
        except Exception as exc:
            self.status_var.set(f"No pude leer la nube: {exc}")
        self.root.after(120_000, self._refresh_quietly)

    # ---------- ajustes ----------

    def save_settings(self) -> None:
        previous_cloud = self.config.cloud.get("database_url", "")
        self.config.cloud = {
            "database_url": self.cloud_url.get().strip(),
            "report_url": self.report_url.get().strip(),
        }
        self.config.scheduler["interval_minutes"] = int(self.interval_var.get())
        self.config.detector["min_drop_pct"] = float(self.drop_var.get())
        self.config.alerts["telegram"] = {
            "enabled": bool(self.tg_enabled.get()),
            "bot_token": self.tg_token.get().strip(),
            "chat_id": self.tg_chat.get().strip(),
        }
        self.config.save()
        self.engine.reload_settings()
        self._schedule_auto_scan()
        estado = "activado" if self.tg_enabled.get() else "desactivado"
        self.settings_status.configure(
            text=f"✓ Ajustes guardados (Telegram {estado})", foreground=OK
        )

        if self.config.cloud["database_url"] != previous_cloud:
            messagebox.showinfo(
                "Reinicia la aplicación",
                "Cambiaste la base de datos.\n\n"
                "Cierra y vuelve a abrir Price Radar para conectarte a la nueva.",
            )

    def test_telegram(self) -> None:
        token = self.tg_token.get().strip()
        if not token:
            self.settings_status.configure(text="Falta el token del bot.", foreground=DANGER)
            return
        if ":" not in token or len(token) < 20:
            self.settings_status.configure(
                text="Eso no parece un token. Debe ser tipo 123456789:AAE... de @BotFather.",
                foreground=DANGER,
            )
            return

        self.settings_status.configure(text="Probando…", foreground=MUTED)

        def finish(message: str, good: bool) -> None:
            self.settings_status.configure(text=message, foreground=OK if good else DANGER)

        def work() -> None:
            chat_id = self.tg_chat.get().strip()
            if not chat_id:
                chat_id = self._discover_chat_id(token)
                if chat_id is None:
                    self.root.after(0, lambda: finish(
                        "No encontré ningún chat. Abre Telegram, escríbele "
                        "cualquier mensaje a tu bot y pulsa Probar otra vez.", False))
                    return
                self.root.after(0, lambda: self.tg_chat.set(chat_id))

            notifier = TelegramNotifier(
                {"telegram": {"enabled": True, "bot_token": token, "chat_id": chat_id}}
            )
            sent, detail = notifier.send_detailed(
                "✅ Price Radar conectado. Aquí llegarán tus alertas."
            )

            def done() -> None:
                if not sent:
                    finish(detail, False)
                    return
                # Si la prueba salió bien, se activa y se guarda solo. Antes había
                # que marcar la casilla y pulsar Guardar: la prueba funcionaba y
                # las alertas nunca llegaban.
                self.tg_enabled.set(True)
                self.save_settings()
                finish("✓ Listo. Revisa Telegram: ya está activado y guardado.", True)

            self.root.after(0, done)

        threading.Thread(target=work, daemon=True).start()

    @staticmethod
    def _discover_chat_id(token: str) -> str | None:
        """Lee getUpdates para ahorrarle al usuario buscar su chat ID a mano."""
        import requests

        try:
            resp = requests.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=15)
            for update in reversed(resp.json().get("result", [])):
                chat = (update.get("message") or update.get("channel_post") or {}).get("chat")
                if chat and chat.get("id"):
                    return str(chat["id"])
        except Exception:
            return None
        return None

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    PriceRadarApp().run()


if __name__ == "__main__":
    main()
