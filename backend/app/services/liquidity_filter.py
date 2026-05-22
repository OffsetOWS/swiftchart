from __future__ import annotations

import logging
from typing import Any

from app.config import get_settings

logger = logging.getLogger(__name__)


VOLUME_KEYS = (
    "perpVolume24h",
    "perp_volume_24h",
    "volume_24h",
    "dayNtlVlm",
    "volume",
)


def perp_volume_24h(market: dict[str, Any]) -> float | None:
    for key in VOLUME_KEYS:
        value = market.get(key)
        if value is None or value == "":
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def is_liquid_perp_market(market: dict[str, Any], min_volume: float | None = None) -> bool:
    threshold = get_settings().min_perp_volume_24h if min_volume is None else min_volume
    volume = perp_volume_24h(market)
    return bool(volume is not None and volume >= threshold)


def skip_low_volume_market(market: dict[str, Any], min_volume: float | None = None) -> bool:
    if is_liquid_perp_market(market, min_volume):
        return False
    symbol = str(market.get("symbol", "")).upper() or "UNKNOWN"
    logger.info("Skipping %s: perp volume below $100k", symbol)
    return True


def filter_liquid_perp_markets(markets: list[dict[str, Any]], min_volume: float | None = None) -> list[dict[str, Any]]:
    return [market for market in markets if not skip_low_volume_market(market, min_volume)]
