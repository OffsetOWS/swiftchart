from __future__ import annotations

from datetime import UTC, datetime, timedelta
import asyncio
import logging

import pandas as pd

from app.config import get_settings
from app.forex.config import CROSS_MARKET_INSTRUMENTS, SUPPORTED_FOREX_PAIRS
from app.forex.market_data import ForexMarketDataService, TIMEFRAME_SECONDS
from app.forex.models import ForexCrossMarketContext, MarketContextComponent

logger = logging.getLogger(__name__)

PRIMARY_TIMEFRAMES = {"15M": "1H", "1H": "1H", "4H": "4H", "1D": "1D"}
HIGHER_TIMEFRAMES = {"15M": "4H", "1H": "4H", "4H": "1D", "1D": None}
SYNTHETIC_USD_PAIRS = ("EURUSD", "GBPUSD", "AUDUSD", "NZDUSD", "USDJPY", "USDCHF", "USDCAD")
SYNTHETIC_USD_MIN_COMPONENTS = 4
SYNTHETIC_USD_COMPONENT_CAP_ATR = 2.5


def _unavailable(instrument: str, timeframe: str, reason: str) -> MarketContextComponent:
    return MarketContextComponent(
        instrument=instrument, source="UNAVAILABLE",
        state="UNAVAILABLE",
        direction="UNAVAILABLE",
        strength_score=0,
        primary_timeframe=timeframe,
        alignment_status="UNAVAILABLE",
        confidence_adjustment=0,
        explanation=reason,
        stale=False,
    )


def _atr(frame: pd.DataFrame) -> float:
    high, low, close = (frame[column].astype(float) for column in ("high", "low", "close"))
    ranges = pd.concat(
        [high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1
    ).max(axis=1)
    value = float(ranges.tail(14).mean())
    return value if value > 0 else 1e-12


def classify_external_market(
    frame: pd.DataFrame,
    *,
    instrument: str,
    timeframe: str,
    now: datetime,
) -> tuple[str, str, float, datetime, bool, str]:
    """Classify a completed-candle series without using a single-candle shortcut."""
    if frame is None or len(frame) < 55:
        raise ValueError("At least 55 completed candles are required.")
    if "complete" in frame and not bool(frame["complete"].astype(bool).all()):
        raise ValueError("Incomplete context candles are not allowed.")
    ordered = frame.sort_values("timestamp").reset_index(drop=True)
    close = ordered["close"].astype(float)
    high = ordered["high"].astype(float)
    low = ordered["low"].astype(float)
    ema20 = close.ewm(span=20, adjust=False).mean()
    ema50 = close.ewm(span=50, adjust=False).mean()
    atr = _atr(ordered)
    score = 0.0

    if close.iloc[-1] > ema20.iloc[-1] > ema50.iloc[-1]:
        score += 25
    elif close.iloc[-1] < ema20.iloc[-1] < ema50.iloc[-1]:
        score -= 25

    fast_slope = float(ema20.iloc[-1] - ema20.iloc[-6]) / atr
    score += 15 if fast_slope >= 0.35 else 8 if fast_slope > 0.08 else -15 if fast_slope <= -0.35 else -8 if fast_slope < -0.08 else 0

    momentum = float(close.iloc[-1] - close.iloc[-6]) / atr
    score += 20 if momentum >= 1 else 10 if momentum >= 0.35 else -20 if momentum <= -1 else -10 if momentum <= -0.35 else 0

    recent, previous = ordered.iloc[-20:], ordered.iloc[-40:-20]
    bullish_structure = float(recent["high"].max()) > float(previous["high"].max()) and float(recent["low"].min()) > float(previous["low"].min())
    bearish_structure = float(recent["high"].max()) < float(previous["high"].max()) and float(recent["low"].min()) < float(previous["low"].min())
    score += 20 if bullish_structure else -20 if bearish_structure else 0

    prior_swing_high = float(high.iloc[-21:-1].max())
    prior_swing_low = float(low.iloc[-21:-1].min())
    distance_high = (float(close.iloc[-1]) - prior_swing_high) / atr
    distance_low = (prior_swing_low - float(close.iloc[-1])) / atr
    if distance_high >= 0:
        score += min(20, 12 + distance_high * 8)
    elif distance_low >= 0:
        score -= min(20, 12 + distance_low * 8)

    score = max(-100.0, min(100.0, score))
    if score >= 65:
        state, direction = ("STRONG_RALLY" if instrument == "WTI" else "STRONG_BULLISH"), "BULLISH"
    elif score >= 25:
        state, direction = ("RALLY" if instrument == "WTI" else "BULLISH"), "BULLISH"
    elif score <= -65:
        state, direction = ("STRONG_SELLOFF" if instrument == "WTI" else "STRONG_BEARISH"), "BEARISH"
    elif score <= -25:
        state, direction = ("SELLOFF" if instrument == "WTI" else "BEARISH"), "BEARISH"
    else:
        state, direction = "NEUTRAL", "NEUTRAL"

    candle_time = pd.Timestamp(ordered.iloc[-1]["timestamp"]).to_pydatetime()
    candle_time = candle_time if candle_time.tzinfo else candle_time.replace(tzinfo=UTC)
    stale_after = timedelta(
        seconds=TIMEFRAME_SECONDS[timeframe] * get_settings().forex_cross_market_stale_multiplier
    )
    stale = now - candle_time.astimezone(UTC) > stale_after
    explanation = (
        f"{instrument} {state.replace('_', ' ').title()} from EMA20/EMA50 alignment, "
        f"five-candle momentum, slope, 20-candle structure and swing distance (score {score:+.0f})."
    )
    return state, direction, abs(round(score, 1)), candle_time.astimezone(UTC), stale, explanation


def _desired_currency_direction(pair: str, trade_direction: str, currency: str) -> int:
    base, quote = pair[:3], pair[3:]
    trade_sign = 1 if trade_direction == "LONG" else -1
    if base == currency:
        return trade_sign
    if quote == currency:
        return -trade_sign
    return 0


def _adjustment(
    *,
    market_direction: str,
    market_state: str,
    desired_direction: int,
    strong_positive: float,
    normal_positive: float,
    normal_negative: float,
    strong_negative: float,
) -> tuple[float, str]:
    if desired_direction == 0:
        return 0.0, "NEUTRAL"
    if market_direction in {"NEUTRAL", "UNAVAILABLE"}:
        return 0.0, "NEUTRAL" if market_direction == "NEUTRAL" else "UNAVAILABLE"
    market_sign = 1 if market_direction == "BULLISH" else -1
    strong = market_state.startswith("STRONG_")
    aligned = market_sign == desired_direction
    if aligned:
        return (strong_positive if strong else normal_positive), ("STRONG_ALIGNMENT" if strong else "ALIGNED")
    return (strong_negative if strong else normal_negative), ("STRONG_CONFLICT" if strong else "CONFLICT")


def _synthetic_usd_contribution(
    frame: pd.DataFrame,
    *,
    pair: str,
    timeframe: str,
    now: datetime,
) -> tuple[float, datetime]:
    """Return an equal-weight USD move in [-1, 1], normalized and capped by ATR."""
    if frame is None or len(frame) < 20:
        raise ValueError(f"Insufficient completed candles for {pair}.")
    if "complete" in frame and not bool(frame["complete"].astype(bool).all()):
        raise ValueError(f"Incomplete synthetic USD candles for {pair}.")
    ordered = frame.sort_values("timestamp").reset_index(drop=True)
    candle_time = pd.Timestamp(ordered.iloc[-1]["timestamp"]).to_pydatetime()
    candle_time = candle_time if candle_time.tzinfo else candle_time.replace(tzinfo=UTC)
    candle_time = candle_time.astimezone(UTC)
    stale_after = timedelta(
        seconds=TIMEFRAME_SECONDS[timeframe] * get_settings().forex_cross_market_stale_multiplier
    )
    if now - candle_time > stale_after:
        raise ValueError(f"Stale synthetic USD candles for {pair}.")
    atr = _atr(ordered)
    normalized_move = (float(ordered["close"].iloc[-1]) - float(ordered["close"].iloc[-6])) / atr
    usd_sign = -1.0 if pair.endswith("USD") else 1.0
    capped = max(-SYNTHETIC_USD_COMPONENT_CAP_ATR, min(SYNTHETIC_USD_COMPONENT_CAP_ATR, normalized_move * usd_sign))
    return capped / SYNTHETIC_USD_COMPONENT_CAP_ATR, candle_time


async def _synthetic_usd_state(
    market_data: ForexMarketDataService,
    *,
    timeframe: str,
    now: datetime,
) -> tuple[str, str, float, datetime, dict[str, float]]:
    frames = await asyncio.gather(
        *[
            market_data.completed_candles(
                SUPPORTED_FOREX_PAIRS[pair], timeframe, limit=80, now=now
            )
            for pair in SYNTHETIC_USD_PAIRS
        ],
        return_exceptions=True,
    )
    contributions: dict[str, float] = {}
    candle_times: list[datetime] = []
    for pair, frame in zip(SYNTHETIC_USD_PAIRS, frames, strict=True):
        if isinstance(frame, Exception):
            continue
        try:
            contribution, candle_time = _synthetic_usd_contribution(
                frame, pair=pair, timeframe=timeframe, now=now
            )
        except (TypeError, ValueError):
            continue
        contributions[pair] = contribution
        candle_times.append(candle_time)
    if len(contributions) < SYNTHETIC_USD_MIN_COMPONENTS:
        raise ValueError(
            f"Synthetic USD needs {SYNTHETIC_USD_MIN_COMPONENTS} fresh components; got {len(contributions)}."
        )
    signed_score = max(-100.0, min(100.0, sum(contributions.values()) / len(contributions) * 100))
    if signed_score >= 65:
        state, direction = "STRONG_BULLISH", "BULLISH"
    elif signed_score >= 25:
        state, direction = "BULLISH", "BULLISH"
    elif signed_score <= -65:
        state, direction = "STRONG_BEARISH", "BEARISH"
    elif signed_score <= -25:
        state, direction = "BEARISH", "BEARISH"
    else:
        state, direction = "NEUTRAL", "NEUTRAL"
    return state, direction, abs(round(signed_score, 1)), min(candle_times), contributions


async def _synthetic_usd_component(
    market_data: ForexMarketDataService,
    *,
    scan_timeframe: str,
    desired_direction: int,
    now: datetime,
) -> MarketContextComponent:
    primary_tf = PRIMARY_TIMEFRAMES[scan_timeframe]
    higher_tf = HIGHER_TIMEFRAMES[scan_timeframe]
    try:
        state, direction, strength, candle_time, contributions = await _synthetic_usd_state(
            market_data, timeframe=primary_tf, now=now
        )
        higher_state = None
        higher_time = None
        if higher_tf:
            higher_state, higher_direction, _, higher_time, _ = await _synthetic_usd_state(
                market_data, timeframe=higher_tf, now=now
            )
            if direction != "NEUTRAL" and higher_direction == direction:
                strength = min(100, strength + 10)
                if not state.startswith("STRONG_") and strength >= 65:
                    state = f"STRONG_{direction}"
        adjustment, alignment = _adjustment(
            market_direction=direction, market_state=state, desired_direction=desired_direction,
            strong_positive=6, normal_positive=3, normal_negative=-4, strong_negative=-7,
        )
        component_text = ", ".join(f"{pair}={value:+.2f}" for pair, value in contributions.items())
        explanation = (
            "Synthetic USD fallback used because provider DXY was unavailable. "
            f"Equal-weight mean of five-candle ATR-normalized USD moves, each capped at ±{SYNTHETIC_USD_COMPONENT_CAP_ATR:g} ATR: "
            f"{component_text}."
        )
        if higher_state:
            explanation += f" Higher-timeframe synthetic state is {higher_state.replace('_', ' ').title()}."
        return MarketContextComponent(
            instrument="DXY", source="SYNTHETIC_USD_BASKET", state=state,
            direction=direction, strength_score=strength, primary_timeframe=primary_tf,
            higher_timeframe_state=higher_state, alignment_status=alignment,
            confidence_adjustment=adjustment, explanation=explanation,
            candle_timestamp=candle_time, higher_timeframe_candle_timestamp=higher_time,
            stale=False,
        )
    except Exception as exc:
        logger.warning("Synthetic USD context unavailable error=%s", type(exc).__name__)
        return _unavailable("DXY", primary_tf, "Provider DXY and synthetic USD basket are unavailable; technical setup is unchanged.")


async def _component(
    market_data: ForexMarketDataService,
    *,
    instrument: str,
    scan_timeframe: str,
    desired_direction: int,
    now: datetime,
) -> MarketContextComponent:
    primary_tf = PRIMARY_TIMEFRAMES[scan_timeframe]
    higher_tf = HIGHER_TIMEFRAMES[scan_timeframe]
    try:
        primary = await market_data.completed_candles(
            CROSS_MARKET_INSTRUMENTS[instrument], primary_tf, limit=100, now=now
        )
        state, direction, strength, candle_time, stale, explanation = classify_external_market(
            primary, instrument=instrument, timeframe=primary_tf, now=now
        )
        higher_state = None
        higher_time = None
        if higher_tf:
            higher = await market_data.completed_candles(
                CROSS_MARKET_INSTRUMENTS[instrument], higher_tf, limit=100, now=now
            )
            higher_state, higher_direction, _, higher_time, higher_stale, _ = classify_external_market(
                higher, instrument=instrument, timeframe=higher_tf, now=now
            )
            stale = stale or higher_stale
            if direction != "NEUTRAL" and higher_direction == direction:
                strength = min(100, strength + 10)
                if not state.startswith("STRONG_") and strength >= 65:
                    state = "STRONG_RALLY" if instrument == "WTI" and direction == "BULLISH" else "STRONG_SELLOFF" if instrument == "WTI" else f"STRONG_{direction}"
            explanation += f" Higher timeframe {higher_tf} is {higher_state.replace('_', ' ').title()}."
        if stale:
            adjustment, alignment = 0.0, "UNAVAILABLE"
            explanation += " Context is stale and has zero scoring influence."
        elif instrument == "DXY":
            adjustment, alignment = _adjustment(
                market_direction=direction, market_state=state, desired_direction=desired_direction,
                strong_positive=6, normal_positive=3, normal_negative=-4, strong_negative=-7,
            )
        else:
            adjustment, alignment = _adjustment(
                market_direction=direction, market_state=state, desired_direction=desired_direction,
                strong_positive=4, normal_positive=2, normal_negative=-3, strong_negative=-5,
            )
        return MarketContextComponent(
            instrument=instrument,
            source="PROVIDER_DXY" if instrument == "DXY" else "PROVIDER_WTI",
            state=state, direction=direction, strength_score=strength,
            primary_timeframe=primary_tf, higher_timeframe_state=higher_state,
            alignment_status=alignment, confidence_adjustment=adjustment,
            explanation=explanation, candle_timestamp=candle_time,
            higher_timeframe_candle_timestamp=higher_time, stale=stale,
        )
    except Exception as exc:
        logger.warning("Cross-market context unavailable instrument=%s error=%s", instrument, type(exc).__name__)
        return _unavailable(instrument, primary_tf, f"{instrument} context unavailable; technical setup is unchanged.")


async def evaluate_cross_market_context(
    pair: str,
    trade_direction: str,
    timeframe: str,
    market_data: ForexMarketDataService,
    *,
    now: datetime | None = None,
) -> ForexCrossMarketContext:
    settings = get_settings()
    evaluated_at = (now or datetime.now(UTC)).astimezone(UTC).replace(microsecond=0)
    usd = None
    oil = None
    if "USD" in pair and settings.forex_dxy_context_enabled:
        usd = await _component(
            market_data, instrument="DXY", scan_timeframe=timeframe,
            desired_direction=_desired_currency_direction(pair, trade_direction, "USD"), now=evaluated_at,
        )
        if usd.state == "UNAVAILABLE":
            usd = await _synthetic_usd_component(
                market_data, scan_timeframe=timeframe,
                desired_direction=_desired_currency_direction(pair, trade_direction, "USD"),
                now=evaluated_at,
            )
    if "CAD" in pair and settings.forex_oil_context_enabled:
        oil = await _component(
            market_data, instrument="WTI", scan_timeframe=timeframe,
            desired_direction=_desired_currency_direction(pair, trade_direction, "CAD"), now=evaluated_at,
        )
    raw = sum(component.confidence_adjustment for component in (usd, oil) if component)
    total = max(
        settings.forex_cross_market_max_negative_adjustment,
        min(settings.forex_cross_market_max_positive_adjustment, raw),
    )
    aligned = [c.instrument for c in (usd, oil) if c and "ALIGN" in c.alignment_status]
    conflicted = [c.instrument for c in (usd, oil) if c and "CONFLICT" in c.alignment_status]
    explanation = (
        f"Context supports {pair} {trade_direction.lower()} via {', '.join(aligned)}."
        if aligned and not conflicted
        else f"Context conflicts via {', '.join(conflicted)}; technical validation remains authoritative."
        if conflicted
        else "Cross-market context is neutral or unavailable; technical validation is unchanged."
    )
    return ForexCrossMarketContext(
        pair=pair, timeframe=timeframe, usd_context=usd, oil_context=oil,
        total_adjustment=round(total, 1), evaluated_at=evaluated_at,
        source_candle_times={
            "DXY": usd.candle_timestamp if usd else None,
            "WTI": oil.candle_timestamp if oil else None,
        },
        stale=any(component.stale for component in (usd, oil) if component),
        explanation=explanation,
    )


def unavailable_cross_market_context(pair: str, timeframe: str, now: datetime) -> ForexCrossMarketContext:
    return ForexCrossMarketContext(
        pair=pair,
        timeframe=timeframe,
        total_adjustment=0,
        evaluated_at=now,
        explanation="Cross-market provider capability is unavailable; technical validation is unchanged.",
    )
