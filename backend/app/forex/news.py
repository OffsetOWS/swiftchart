from __future__ import annotations

import os

from app.forex.config import NEWS_KEYWORDS


def forex_news_risk(pair: str | None = None) -> tuple[str, str]:
    configured = os.getenv("FOREX_NEWS_RISK", "LOW").strip().upper()
    risk = configured if configured in {"LOW", "MEDIUM", "HIGH"} else "LOW"
    if risk == "HIGH":
        return risk, "High-impact news risk"
    if risk == "MEDIUM":
        return risk, f"Monitor scheduled events: {', '.join(NEWS_KEYWORDS[:4])}."
    return risk, "No high-impact news risk detected."
