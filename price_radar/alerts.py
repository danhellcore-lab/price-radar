from __future__ import annotations

import html
import logging
from typing import Any

import requests

log = logging.getLogger(__name__)

# Traducción de los errores que devuelve Telegram, que llegan en inglés y sin
# contexto. Sin esto el usuario solo ve "no se pudo enviar".
API_ERRORS = {
    "unauthorized": "El token del bot no es válido. Cópialo otra vez desde @BotFather.",
    "chat not found": "Ese Chat ID no existe. Escríbele un mensaje a tu bot y vuelve a probar.",
    "bot was blocked": "Bloqueaste al bot en Telegram. Desbloquéalo y reintenta.",
    "chat_id is empty": "Falta el Chat ID.",
    "can't parse entities": "El mensaje tenía un formato inválido (esto es un fallo de la app).",
}


def explain(detail: str) -> str:
    lowered = detail.lower()
    for needle, message in API_ERRORS.items():
        if needle in lowered:
            return message
    return detail


class TelegramNotifier:
    def __init__(self, settings: dict[str, Any]):
        tg = (settings or {}).get("telegram", {})
        self.enabled = bool(tg.get("enabled"))
        self.token = (tg.get("bot_token") or "").strip()
        self.chat_id = str(tg.get("chat_id") or "").strip()
        if self.enabled and not (self.token and self.chat_id):
            log.warning("Telegram habilitado pero falta bot_token o chat_id; se desactiva.")
            self.enabled = False

    def send(self, text: str) -> bool:
        ok, detail = self.send_detailed(text)
        if not ok:
            log.error("No se pudo enviar la alerta por Telegram: %s", detail)
        return ok

    def send_detailed(self, text: str) -> tuple[bool, str]:
        """Igual que `send`, pero devuelve el motivo del fallo para la interfaz."""
        if not self.enabled:
            log.info("[alerta no enviada, Telegram desactivado]\n%s", text)
            return False, "Telegram está desactivado en Ajustes."

        try:
            resp = requests.post(
                f"https://api.telegram.org/bot{self.token}/sendMessage",
                json={
                    "chat_id": self.chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": False,
                },
                timeout=15,
            )
        except requests.RequestException as exc:
            return False, f"No hay conexión con Telegram ({type(exc).__name__})."

        if resp.status_code == 200:
            return True, "enviado"

        detail = ""
        try:
            detail = resp.json().get("description", "")
        except ValueError:
            detail = resp.text[:200]
        log.error("Telegram respondió %s: %s", resp.status_code, detail)
        return False, explain(detail or f"HTTP {resp.status_code}")


def format_alert(name: str, url: str, currency: str, price: float, baseline: float, reason: str) -> str:
    """Mensaje de alerta.

    Los textos se escapan porque van con parse_mode HTML y los nombres de
    producto traen `&` y comillas ("Taladro & Accesorios", 'Monitor 27"'). Sin
    escapar, Telegram rechaza el mensaje entero con "can't parse entities" y la
    alerta se pierde en silencio.
    """
    safe_name = html.escape(name)
    safe_reason = html.escape(reason)
    safe_url = html.escape(url, quote=False)
    # El separador de miles se cambia solo sobre los números; hacerlo sobre todo
    # el mensaje destrozaría las comas del nombre del producto.
    now = f"{price:,.0f}".replace(",", ".")
    usual = f"{baseline:,.0f}".replace(",", ".")
    return (
        "🚨 <b>Posible error de precio</b>\n\n"
        f"<b>{safe_name}</b>\n"
        f"Precio actual: <b>{currency} {now}</b>\n"
        f"Precio habitual: {currency} {usual}\n"
        f"Motivo: {safe_reason}\n\n"
        f"{safe_url}"
    )
