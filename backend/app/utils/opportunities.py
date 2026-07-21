from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal


SetupFamily = Literal[
    "breakout",
    "trend_continuation",
    "range_mean_reversion",
    "regime_transition",
]


def setup_family_from_regime(regime: str | None) -> SetupFamily | None:
    normalized = str(regime or "").upper()
    if normalized in {"BREAKOUT", "BREAKDOWN"}:
        return "breakout"
    if normalized in {"TRENDING_UP", "TRENDING_DOWN"}:
        return "trend_continuation"
    if normalized == "RANGE_BOUND":
        return "range_mean_reversion"
    if normalized in {"TRANSITION_TO_BULLISH", "TRANSITION_TO_BEARISH"}:
        return "regime_transition"
    return None


def canonical_candle_time(value: datetime | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        parsed = value
    normalized = parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return normalized.astimezone(UTC).replace(microsecond=0).isoformat()


def canonical_opportunity_key(
    *,
    exchange: str,
    symbol: str,
    timeframe: str,
    direction: str,
    setup_family: SetupFamily | None,
    strategy_version: str | None = None,
    signal_candle_time: datetime | str | None,
) -> str | None:
    candle = canonical_candle_time(signal_candle_time)
    if setup_family is None or candle is None:
        return None
    parts = [
        exchange.lower(),
        symbol.upper(),
        timeframe.lower(),
        direction.upper(),
        setup_family,
    ]
    if strategy_version:
        parts.append(strategy_version.lower())
    parts.append(candle)
    return "|".join(parts)
