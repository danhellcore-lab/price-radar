from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

# Constante que hace que la MAD sea un estimador consistente de sigma
# para una distribución normal.
MAD_TO_SIGMA = 1.4826


@dataclass
class Verdict:
    is_anomaly: bool
    baseline: float
    drop_pct: float
    mad_score: float
    reason: str


class Detector:
    """Detección robusta de errores de precio vía mediana + MAD.

    Se usa mediana/MAD en vez de media/desviación estándar porque un par de
    outliers (justamente lo que buscamos) contaminarían el baseline.
    """

    def __init__(self, settings: dict[str, Any]):
        self.min_history = settings.get("min_history", 8)
        self.min_drop_pct = settings.get("min_drop_pct", 35.0)
        self.mad_threshold = settings.get("mad_threshold", 4.0)
        self.window_days = settings.get("window_days", 30)
        self.cooldown_hours = settings.get("cooldown_hours", 12)

    def evaluate(self, price: float, history: list[dict[str, Any]]) -> Verdict:
        """`history` son observaciones previas (sin incluir `price`)."""
        prices = [h["price"] for h in history if h.get("price")]

        if len(prices) < self.min_history:
            return Verdict(
                False, price, 0.0, 0.0,
                f"historial insuficiente ({len(prices)}/{self.min_history})",
            )

        baseline = statistics.median(prices)
        if baseline <= 0:
            return Verdict(False, baseline, 0.0, 0.0, "baseline inválido")

        drop_pct = (baseline - price) / baseline * 100.0

        deviations = [abs(p - baseline) for p in prices]
        mad = statistics.median(deviations)
        if mad > 0:
            mad_score = abs(price - baseline) / (mad * MAD_TO_SIGMA)
        else:
            # Precio históricamente constante: cualquier cambio real es sospechoso.
            mad_score = float("inf") if price != baseline else 0.0

        if drop_pct < self.min_drop_pct:
            return Verdict(
                False, baseline, drop_pct, mad_score,
                f"caída {drop_pct:.1f}% bajo el umbral de {self.min_drop_pct}%",
            )
        if mad_score < self.mad_threshold:
            return Verdict(
                False, baseline, drop_pct, mad_score,
                f"MAD-score {mad_score:.1f} bajo el umbral de {self.mad_threshold}",
            )

        return Verdict(
            True, baseline, drop_pct, mad_score,
            f"caída de {drop_pct:.1f}% vs mediana ({baseline:,.0f}); MAD-score {mad_score:.1f}",
        )

    def in_cooldown(self, last_alert: dict[str, Any] | None) -> bool:
        if not last_alert:
            return False
        ts = datetime.fromisoformat(last_alert["ts"])
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - ts < timedelta(hours=self.cooldown_hours)
