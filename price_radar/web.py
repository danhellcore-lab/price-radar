from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

from .config import Config
from .engine import Engine
from .storage import Storage

TEMPLATE = Path(__file__).parent / "templates" / "dashboard.html"


def create_app(config: Config | None = None) -> FastAPI:
    config = config or Config.load()
    storage = Storage()
    engine = Engine(config, storage)

    app = FastAPI(title="Price Radar")

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return TEMPLATE.read_text(encoding="utf-8")

    @app.get("/api/products")
    def products() -> JSONResponse:
        out = []
        for p in storage.products():
            last = storage.last_observation(p["id"])
            hist = storage.history(p["id"], days=config.detector.get("window_days", 30))
            prices = [h["price"] for h in hist]
            out.append(
                {
                    **p,
                    "last_price": last["price"] if last else None,
                    "last_ts": last["ts"] if last else None,
                    "last_ok": bool(last["ok"]) if last else None,
                    "last_error": last["error"] if last else None,
                    "observations": len(prices),
                    "min": min(prices) if prices else None,
                    "max": max(prices) if prices else None,
                }
            )
        return JSONResponse(out)

    @app.get("/api/products/{product_id}/history")
    def history(product_id: int, days: int = 90) -> JSONResponse:
        return JSONResponse(storage.history(product_id, days=days))

    @app.get("/api/alerts")
    def alerts(limit: int = 50) -> JSONResponse:
        return JSONResponse(storage.recent_alerts(limit))

    @app.post("/api/run")
    def run_now() -> JSONResponse:
        return JSONResponse(engine.run_cycle())

    return app
