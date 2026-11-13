from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pandas as pd

from app.models.schemas import LiquiditySweep, Zone
from app.strategy.market_structure import volume_confirmation
from app.strategy.support_resistance import average_true_range


TIMEFRAME_MINUTES = {
    "30m": 30,
    "1h": 60,
    "2h": 120,
    "3h": 180,
    "4h": 240,
    "6h": 360,
    "8h": 480,
    "12h": 720,
    "1d": 1440,
}


@dataclass(frozen=True)
class StrictTrendShortResult:
    eligible: bool
    trigger_type: str | None
    confirmation_type: str | None
    trigger_candle_time: datetime | None
    trigger_candle_completed: bool | None


def _utc(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, pd.Timestamp):
        value = value.to_pydatetime()
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)


def _timeframe_delta(timeframe: str) -> timedelta | None:
    minutes = TIMEFRAME_MINUTES.get(str(timeframe).lower())
    return timedelta(minutes=minutes) if minutes else None


def candle_is_completed(timestamp, timeframe: str, *, as_of: datetime | None = None) -> bool:
    opened_at = _utc(timestamp)
    duration = _timeframe_delta(timeframe)
    now = _utc(as_of) or datetime.now(UTC)
    return bool(opened_at and duration and opened_at + duration <= now)


def _latest_completed_index(df: pd.DataFrame, timeframe: str, as_of: datetime) -> int | None:
    if "timestamp" not in df:
        return None
    for index in range(len(df) - 1, -1, -1):
        if candle_is_completed(df.iloc[index]["timestamp"], timeframe, as_of=as_of):
            return index
    return None


def _bearish_momentum(df: pd.DataFrame) -> bool:
    if len(df) < 7:
        return False
    close = df["close"].astype(float)
    ema21 = close.ewm(span=21, adjust=False).mean()
    ema55 = close.ewm(span=55, adjust=False).mean()
    return bool(
        float(close.iloc[-1]) < float(ema21.iloc[-1]) < float(ema55.iloc[-1])
        and float(close.pct_change(6).iloc[-1]) < 0
    )


def _confirmed_bearish_sweep(
    sweeps: list[LiquiditySweep],
    timeframe: str,
    *,
    as_of: datetime,
    latest_completed_time: datetime,
) -> datetime | None:
    duration = _timeframe_delta(timeframe)
    if duration is None:
        return None
    candidates: list[datetime] = []
    for sweep in sweeps:
        sweep_time = _utc(sweep.candle_time)
        if sweep.direction != "bearish" or sweep.confirmation_status != "confirmed" or sweep_time is None:
            continue
        confirmation_candle_time = sweep_time + duration
        confirmation_completed = confirmation_candle_time + duration <= as_of
        # A confirmed sweep is an entry-timing trigger only on its confirmation
        # candle.  Older confirmed sweeps remain useful context, but cannot make
        # a new opportunity strict-eligible.
        if confirmation_completed and confirmation_candle_time == latest_completed_time:
            candidates.append(confirmation_candle_time)
    return max(candidates) if candidates else None


def evaluate_strict_trend_short(
    *,
    regime: str,
    direction: str,
    df: pd.DataFrame,
    timeframe: str,
    support: Zone | None,
    resistance: Zone | None,
    sweeps: list[LiquiditySweep],
    htf_bias: str,
    normalized_position: float | None = None,
    as_of: datetime | None = None,
) -> StrictTrendShortResult:
    """Shadow-only trend-short eligibility. It never changes production admission."""
    if str(regime).upper() != "TRENDING_DOWN" or str(direction).upper() != "SHORT":
        return StrictTrendShortResult(False, None, None, None, None)

    # Position is accepted for audit/test visibility but intentionally cannot qualify the rule.
    _ = normalized_position
    now = _utc(as_of) or datetime.now(UTC)
    completed_index = _latest_completed_index(df, timeframe, now)
    if completed_index is None:
        return StrictTrendShortResult(False, None, None, None, False)

    completed = df.iloc[: completed_index + 1].copy()
    candle = completed.iloc[-1]
    candle_time = _utc(candle["timestamp"])
    if candle_time is None:
        return StrictTrendShortResult(False, None, None, None, False)
    high = float(candle["high"])
    close = float(candle["close"])
    previous_close = float(completed.iloc[-2]["close"]) if len(completed) >= 2 else None
    atr = average_true_range(completed)

    trigger_type: str | None = None
    trigger_time = candle_time
    if resistance is not None and high >= float(resistance.lower) - atr * 0.35 and close < float(resistance.lower):
        trigger_type = "resistance_rejection"
    elif support is not None and high >= float(support.lower) - atr * 0.35 and close < float(support.lower):
        trigger_type = "failed_reclaim"
    elif support is not None and high >= float(support.lower) - atr * 0.5 and close < float(support.upper):
        trigger_type = "completed_bearish_retest"
    else:
        sweep_confirmation_time = _confirmed_bearish_sweep(
            sweeps,
            timeframe,
            as_of=now,
            latest_completed_time=candle_time,
        )
        if sweep_confirmation_time is not None:
            trigger_type = "bearish_liquidity_sweep"
            trigger_time = sweep_confirmation_time
        elif (
            support is not None
            and previous_close is not None
            and previous_close >= float(support.lower)
            and close < float(support.lower) - atr * 0.12
        ):
            trigger_type = "confirmed_structure_break"

    confirmation_type: str | None = None
    if htf_bias == "HTF_BEARISH":
        confirmation_type = "htf_bearish_alignment"
    elif _bearish_momentum(completed):
        confirmation_type = "bearish_momentum"
    elif volume_confirmation(completed):
        confirmation_type = "volume_confirmation"

    return StrictTrendShortResult(
        eligible=bool(trigger_type and confirmation_type),
        trigger_type=trigger_type,
        confirmation_type=confirmation_type,
        trigger_candle_time=trigger_time if trigger_type else None,
        trigger_candle_completed=True if trigger_type else None,
    )
