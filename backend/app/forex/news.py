from __future__ import annotations

import os

from app.forex.config import NEWS_KEYWORDS
from app.forex.models import ForexNewsRisk


def forex_news_risk(pair: str | None = None) -> tuple[ForexNewsRisk, str]:
    configured = os.getenv("FOREX_NEWS_RISK", "LOW").strip().upper()
    if configured in {"LOW", "MEDIUM", "HIGH"}:
        risk: ForexNewsRisk = configured  # type: ignore[assignment]
    else:
        risk = "LOW"
    if risk == "HIGH":
        return risk, "High-impact news risk"
    if risk == "MEDIUM":
        return risk, f"Monitor scheduled events: {', '.join(NEWS_KEYWORDS[:4])}."
    return risk, "No high-impact placeholder news risk detected."
