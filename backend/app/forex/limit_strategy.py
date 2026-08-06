from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
from uuid import uuid4

import pandas as pd

from app.config import get_settings
from app.forex.config import ForexPairConfig
from app.forex.market_data import TIMEFRAME_SECONDS
from app.forex.models import FairValueGap, ForexCrossMarketContext, ForexLimitOpportunity

STRATEGY_ID = "liquidity_sweep_fvg_limit_v1"
STRATEGY_FAMILY = "liquidity_sweep_fvg_limit"
ALLOWED_ORDER_TYPES = frozenset({"BUY_LIMIT", "SELL_LIMIT"})
ALLOWED_INITIAL_STATES = frozenset({"WAIT_FOR_RETEST", "PENDING_LIMIT"})


def _atr(frame: pd.DataFrame) -> float:
    high, low, close = (frame[column].astype(float) for column in ("high", "low", "close"))
    ranges = pd.concat(
        [high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1
    ).max(axis=1)
    return float(ranges.tail(14).mean())


def _utc(value) -> datetime:
    result = pd.Timestamp(value).to_pydatetime()
    return result.astimezone(UTC) if result.tzinfo else result.replace(tzinfo=UTC)


def _entry_price(mode: str, *, lower: float, upper: float, direction: str, candle1) -> float:
    if mode == "FVG_NEAR_EDGE":
        return upper if direction == "LONG" else lower
    if mode == "FVG_DEEP_EDGE":
        return lower if direction == "LONG" else upper
    if mode == "FVG_ORDER_BLOCK_CONFLUENCE":
        return max(lower, min(upper, float(candle1["open"])))
    return (lower + upper) / 2


def _expiry_count(timeframe: str) -> int:
    settings = get_settings()
    return {
        "1H": settings.forex_fvg_expiry_1h_candles,
        "4H": settings.forex_fvg_expiry_4h_candles,
        "1D": settings.forex_fvg_expiry_1d_candles,
    }[timeframe]


def _empty_context(pair: str, timeframe: str, now: datetime) -> ForexCrossMarketContext:
    return ForexCrossMarketContext(
        pair=pair,
        timeframe=timeframe,
        total_adjustment=0,
        evaluated_at=now,
        explanation="Cross-market context was unavailable; technical validation was unchanged.",
    )


def detect_liquidity_sweep_fvg_limit(
    pair: ForexPairConfig,
    candles: pd.DataFrame,
    *,
    timeframe: str,
    htf_bias: str,
    context: ForexCrossMarketContext | None = None,
    news_risk: str = "LOW",
    spread_ok: bool = True,
    session_label: str = "Unknown",
    now: datetime | None = None,
) -> tuple[ForexLimitOpportunity | None, str]:
    """Return a pending limit opportunity only; this function cannot emit a market order."""
    settings = get_settings()
    evaluated_at = (now or datetime.now(UTC)).astimezone(UTC).replace(microsecond=0)
    timeframe = timeframe.upper()
    if timeframe not in {"1H", "4H", "1D"}:
        return None, "Unsupported limit-strategy timeframe."
    if len(candles) < max(35, settings.forex_fvg_sweep_lookback + 8):
        return None, "Insufficient completed candle history."
    if "complete" in candles and not bool(candles["complete"].astype(bool).all()):
        return None, "Incomplete candles are not allowed."
    if news_risk == "HIGH":
        return None, "High-impact news lockout."
    if not spread_ok:
        return None, "Spread is outside the configured limit."

    frame = candles.sort_values("timestamp").reset_index(drop=True)
    c1, displacement, c3 = frame.iloc[-3], frame.iloc[-2], frame.iloc[-1]
    atr = _atr(frame.iloc[:-1])
    if atr <= 0:
        return None, "ATR is unavailable."
    min_gap = max(settings.forex_fvg_min_gap_pips * pair.pip_size, atr * settings.forex_fvg_min_gap_atr)
    body = abs(float(displacement["close"]) - float(displacement["open"]))
    displacement_ok = body >= atr * settings.forex_fvg_displacement_atr

    bullish_gap = float(c1["high"]) < float(c3["low"])
    bearish_gap = float(c1["low"]) > float(c3["high"])
    bullish_size = float(c3["low"]) - float(c1["high"])
    bearish_size = float(c1["low"]) - float(c3["high"])
    bullish_displacement = displacement_ok and float(displacement["close"]) > float(displacement["open"])
    bearish_displacement = displacement_ok and float(displacement["close"]) < float(displacement["open"])

    if bullish_gap and bullish_size >= min_gap and bullish_displacement:
        direction, order_type = "LONG", "BUY_LIMIT"
        lower, upper, gap_size = float(c1["high"]), float(c3["low"]), bullish_size
    elif bearish_gap and bearish_size >= min_gap and bearish_displacement:
        direction, order_type = "SHORT", "SELL_LIMIT"
        lower, upper, gap_size = float(c3["high"]), float(c1["low"]), bearish_size
    else:
        if not displacement_ok:
            return None, "Liquidity event lacked ATR-qualified displacement."
        return None, "Strict three-candle FVG was not created or was too small."

    allowed_biases = {"LONG": {"BULLISH", "TRANSITIONING_BULLISH"}, "SHORT": {"BEARISH", "TRANSITIONING_BEARISH"}}
    if htf_bias.upper() not in allowed_biases[direction]:
        return None, "Higher-timeframe bias does not allow this limit direction."

    sweep_index = len(frame) - 3
    swing_window = frame.iloc[
        max(0, sweep_index - settings.forex_fvg_sweep_lookback) : max(0, sweep_index - 2)
    ]
    if len(swing_window) < 5:
        return None, "No meaningful confirmed swing preceded displacement."
    internal = frame.iloc[max(0, sweep_index - 6) : sweep_index + 1]
    if direction == "LONG":
        swing_level = float(swing_window["low"].min())
        swept = float(c1["low"]) < swing_level and float(c1["close"]) > swing_level
        structure_broken = float(displacement["close"]) > float(internal["high"].max())
        sweep_extreme = float(c1["low"])
    else:
        swing_level = float(swing_window["high"].max())
        swept = float(c1["high"]) > swing_level and float(c1["close"]) < swing_level
        structure_broken = float(displacement["close"]) < float(internal["low"].min())
        sweep_extreme = float(c1["high"])
    if not swept:
        return None, "No confirmed liquidity sweep and reclaim preceded the FVG."
    if not structure_broken and body < atr * settings.forex_fvg_displacement_atr * 1.35:
        return None, "Displacement did not break internal structure or expand sufficiently."

    entry_mode = settings.forex_fvg_entry_mode.upper()
    if entry_mode not in {"FVG_NEAR_EDGE", "FVG_MIDPOINT", "FVG_DEEP_EDGE", "FVG_ORDER_BLOCK_CONFLUENCE"}:
        entry_mode = "FVG_MIDPOINT"
    entry = _entry_price(entry_mode, lower=lower, upper=upper, direction=direction, candle1=c1)
    current = float(c3["close"])
    if (direction == "LONG" and entry >= current) or (direction == "SHORT" and entry <= current):
        return None, "FVG entry is not a retracement limit beyond current price."

    stop_buffer = max(atr * 0.10, pair.pip_size)
    stop = sweep_extreme - stop_buffer if direction == "LONG" else sweep_extreme + stop_buffer
    risk = abs(entry - stop)
    risk_pips = risk / pair.pip_size
    if risk_pips < settings.forex_min_stop_pips or risk_pips > settings.forex_max_stop_pips:
        return None, "Structural stop width is outside configured risk limits."

    history = frame.iloc[:-3]
    if direction == "LONG":
        targets = sorted({float(value) for value in history["high"] if float(value) > current})
    else:
        targets = sorted({float(value) for value in history["low"] if float(value) < current}, reverse=True)
    if len(targets) < 2:
        return None, "Validated opposing liquidity targets are unavailable."
    tp1, tp2 = targets[0], targets[-1]
    rr1, rr2 = abs(tp1 - entry) / risk, abs(tp2 - entry) / risk
    if rr2 < settings.default_min_rr:
        return None, "Reward-to-risk is below the configured minimum."
    if (direction == "LONG" and current >= tp1) or (direction == "SHORT" and current <= tp1):
        return None, "Target was reached before the limit opportunity could be created."

    context = context or _empty_context(pair.pair, timeframe, evaluated_at)
    technical_score = min(95.0, 76 + min(8, gap_size / atr * 20) + min(6, body / atr * 3))
    final_score = max(0.0, min(100.0, technical_score + context.total_adjustment))
    risk_amount = settings.default_account_size * settings.forex_risk_percentage_per_trade / 100
    suggested_position_size = max(
        settings.forex_min_position_size,
        min(settings.forex_max_position_size, risk_amount / risk),
    )
    fvg_time = _utc(c3["timestamp"])
    sweep_time = _utc(c1["timestamp"])
    dedupe_raw = "|".join(
        [pair.pair, timeframe, STRATEGY_ID, direction, sweep_time.isoformat(), fvg_time.isoformat()]
    )
    dedupe_key = hashlib.sha256(dedupe_raw.encode("utf-8")).hexdigest()
    expiry_count = _expiry_count(timeframe)
    precision = 3 if pair.pip_size >= 0.01 else 5
    opportunity = ForexLimitOpportunity(
        id=str(uuid4()), pair=pair.pair, timeframe=timeframe,
        shadow_mode=settings.forex_liquidity_fvg_limit_shadow_mode,
        auto_execution_enabled=False, order_type=order_type,
        opportunity_status="WAIT_FOR_RETEST", direction=direction,
        entry_price=round(entry, precision), entry_zone_low=round(lower, precision),
        entry_zone_high=round(upper, precision), entry_mode=entry_mode,
        current_price=round(current, precision),
        market_session=session_label,
        distance_to_entry_pips=round(abs(current - entry) / pair.pip_size, 1),
        sweep_level=round(swing_level, precision), sweep_extreme=round(sweep_extreme, precision),
        sweep_candle_time=sweep_time, displacement_candle_time=_utc(displacement["timestamp"]),
        fvg=FairValueGap(
            lower=round(lower, precision), upper=round(upper, precision),
            midpoint=round((lower + upper) / 2, precision),
            gap_size_pips=round(gap_size / pair.pip_size, 1),
            gap_size_atr=round(gap_size / atr, 3), creation_candle_time=fvg_time,
        ),
        stop_loss=round(stop, precision), take_profit_1=round(tp1, precision),
        take_profit_2=round(tp2, precision), risk_pips=round(risk_pips, 1),
        reward_1_pips=round(abs(tp1 - entry) / pair.pip_size, 1),
        reward_2_pips=round(abs(tp2 - entry) / pair.pip_size, 1),
        risk_reward_1=round(rr1, 2), risk_reward_2=round(rr2, 2),
        suggested_position_size=round(suggested_position_size, 2),
        expiry_time=fvg_time + timedelta(seconds=TIMEFRAME_SECONDS[timeframe] * expiry_count),
        expiry_candle_count=expiry_count, technical_score=round(technical_score, 1),
        final_score=round(final_score, 1), context=context,
        reasoning=[
            f"{'Sell-side' if direction == 'LONG' else 'Buy-side'} liquidity swept and reclaimed.",
            f"{direction.title()} displacement broke structure or exceeded expansion threshold.",
            f"Strict three-candle {direction.lower()} FVG created.",
            "Waiting for retracement; this is not an active trade.",
        ],
        dedupe_key=dedupe_key, created_at=evaluated_at, updated_at=evaluated_at,
        lifecycle_events=[{"event": "WAIT_FOR_RETEST", "at": evaluated_at.isoformat()}],
    )
    if opportunity.order_type not in ALLOWED_ORDER_TYPES or opportunity.opportunity_status not in ALLOWED_INITIAL_STATES:
        raise AssertionError("Limit strategy attempted to emit a non-limit signal.")
    return opportunity, "Qualified shadow-mode limit opportunity."
