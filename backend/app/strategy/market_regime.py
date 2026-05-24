from __future__ import annotations

from datetime import UTC, datetime
import logging

import pandas as pd

from app.models.schemas import MarketRegimeSnapshot, MarketRegimeType
from app.strategy.market_structure import higher_timeframe_bias, trend_direction
from app.strategy.support_resistance import average_true_range, detect_swings


MIN_REGIME_CONFIDENCE = 65.0

logger = logging.getLogger(__name__)


def _ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def _rsi(close: pd.Series, period: int = 14) -> float:
    if len(close) < period + 2:
        return 50.0
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = -delta.clip(upper=0).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / loss.replace(0, 1e-9)
    return float((100 - (100 / (1 + rs))).iloc[-1])


def _adx(df: pd.DataFrame, period: int = 14) -> float | None:
    if len(df) < period * 2 + 2:
        return None
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = df["close"].astype(float)
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
    minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)
    true_range = pd.concat(
        [
            high - low,
            (high - close.shift()).abs(),
            (low - close.shift()).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr = true_range.ewm(alpha=1 / period, adjust=False).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr.replace(0, 1e-9)
    minus_di = 100 * minus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr.replace(0, 1e-9)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, 1e-9)
    value = float(dx.ewm(alpha=1 / period, adjust=False).mean().iloc[-1])
    return round(value, 2)


def _label(score: float) -> str:
    if score >= 60:
        return "Strong Bullish"
    if score >= 20:
        return "Weak Bullish"
    if score <= -60:
        return "Strong Bearish"
    if score <= -20:
        return "Weak Bearish"
    return "Ranging / Neutral"


def _bias(label: str, regime_type: MarketRegimeType) -> tuple[str, str, str]:
    if regime_type == "TRANSITION_TO_BULLISH":
        return "Bullish transition", "Wait for bullish confirmation", "Shorts disabled during bullish transition"
    if regime_type == "TRANSITION_TO_BEARISH":
        return "Bearish transition", "Longs disabled during bearish transition", "Wait for bearish confirmation"
    if regime_type in {"TRENDING_UP", "BREAKOUT"} or "Bullish" in label:
        return "Long bias", "Prioritize longs", "Counter-trend shorts require strong reversal confirmation"
    if regime_type in {"TRENDING_DOWN", "BREAKDOWN"} or "Bearish" in label:
        return "Short bias", "Counter-trend longs require strong reversal confirmation", "Prioritize shorts"
    if regime_type == "CHOP":
        return "No-trade", "Longs require clean range support", "Shorts require clean range resistance"
    return "Balanced", "Range/mean-reversion longs allowed", "Range/mean-reversion shorts allowed"


def _recent_score_slope(df: pd.DataFrame, htf_dfs: list[pd.DataFrame] | None) -> float:
    if len(df) < 85:
        return 0.0
    previous = df.iloc[:-12].copy()
    if len(previous) < 55:
        return 0.0
    return round(regime_score_from_dataframe(df, htf_dfs) - regime_score_from_dataframe(previous, htf_dfs), 1)


def _structure_profile(df: pd.DataFrame) -> dict[str, float | str | bool]:
    if len(df) < 60:
        return {
            "structure": "Insufficient structure",
            "bullish_cycles": 0,
            "bearish_cycles": 0,
            "is_range": False,
            "range_high": None,
            "range_low": None,
            "breakout_close": False,
            "breakdown_close": False,
            "breakout_confirmed": False,
            "breakdown_confirmed": False,
            "recent_support": None,
            "recent_resistance": None,
            "structural_support_break": False,
            "structural_resistance_reclaim": False,
            "lower_high_lower_low": False,
            "higher_high_higher_low": False,
            "bearish_ema_momentum": False,
            "bullish_ema_momentum": False,
            "bearish_structure_active": False,
            "bullish_structure_active": False,
            "structure_reclaimed_bullish": False,
            "structure_reclaimed_bearish": False,
            "bias_flip_trigger": None,
            "volatility_compression": False,
            "distance_from_range_boundary": 0.0,
        }

    recent = df.tail(90).reset_index(drop=True)
    swings = detect_swings(recent)
    highs = swings.loc[swings["swing_high"], "high"].tail(4).astype(float).tolist()
    lows = swings.loc[swings["swing_low"], "low"].tail(4).astype(float).tolist()
    bullish_cycles = sum(1 for idx in range(1, min(len(highs), len(lows))) if highs[idx] > highs[idx - 1] and lows[idx] > lows[idx - 1])
    bearish_cycles = sum(1 for idx in range(1, min(len(highs), len(lows))) if highs[idx] < highs[idx - 1] and lows[idx] < lows[idx - 1])

    close = recent["close"].astype(float)
    price = float(close.iloc[-1])
    atr = average_true_range(recent)
    prior_range = recent.iloc[-63:-3] if len(recent) >= 66 else recent.iloc[:-3]
    range_high = float(prior_range["high"].max())
    range_low = float(prior_range["low"].min())
    range_width = max(range_high - range_low, 1e-9)
    distance_from_boundary = min(abs(price - range_high), abs(price - range_low)) / range_width

    recent_range = float(recent["high"].tail(20).max() - recent["low"].tail(20).min())
    previous_range = float(recent["high"].iloc[-45:-20].max() - recent["low"].iloc[-45:-20].min()) if len(recent) >= 45 else recent_range
    volatility_compression = previous_range > 0 and recent_range < previous_range * 0.75

    range_tolerance = max(atr * 0.8, range_width * 0.035)
    high_touches = int((prior_range["high"] >= range_high - range_tolerance).sum())
    low_touches = int((prior_range["low"] <= range_low + range_tolerance).sum())
    range_respected = high_touches >= 2 and low_touches >= 2 and bullish_cycles < 2 and bearish_cycles < 2 and volatility_compression

    last = recent.iloc[-1]
    previous = recent.iloc[-2]
    candle_body = abs(float(last["close"]) - float(last["open"]))
    recent_support = float(recent["low"].iloc[-34:-3].min()) if len(recent) >= 37 else float(prior_range["low"].min())
    recent_resistance = float(recent["high"].iloc[-34:-3].max()) if len(recent) >= 37 else float(prior_range["high"].max())
    breakout_close = price > range_high + atr * 0.15
    breakdown_close = price < range_low - atr * 0.15
    structural_support_break = price < recent_support - atr * 0.12
    structural_resistance_reclaim = price > recent_resistance + atr * 0.12
    continuation = candle_body >= atr * 0.75
    retest_holds = float(last["low"]) <= range_high + atr * 0.35 and price > range_high
    retest_fails = float(last["high"]) >= range_low - atr * 0.35 and price < range_low
    two_closes_up = breakout_close and float(previous["close"]) > range_high
    two_closes_down = breakdown_close and float(previous["close"]) < range_low
    recent_highs = recent["high"].tail(24)
    recent_lows = recent["low"].tail(24)
    prior_highs = recent["high"].iloc[-48:-24] if len(recent) >= 48 else recent["high"].iloc[:-24]
    prior_lows = recent["low"].iloc[-48:-24] if len(recent) >= 48 else recent["low"].iloc[:-24]
    lower_high_lower_low = (
        (bearish_cycles >= 1 and len(highs) >= 2 and len(lows) >= 2)
        or (
            not prior_highs.empty
            and not prior_lows.empty
            and float(recent_highs.max()) < float(prior_highs.max()) + atr * 0.2
            and float(recent_lows.min()) < float(prior_lows.min()) - atr * 0.1
        )
    )
    higher_high_higher_low = (
        (bullish_cycles >= 1 and len(highs) >= 2 and len(lows) >= 2)
        or (
            not prior_highs.empty
            and not prior_lows.empty
            and float(recent_highs.max()) > float(prior_highs.max()) + atr * 0.1
            and float(recent_lows.min()) > float(prior_lows.min()) - atr * 0.2
        )
    )
    ema21 = close.ewm(span=21, adjust=False).mean()
    ema55 = close.ewm(span=55, adjust=False).mean()
    ema_slope = (float(ema21.iloc[-1]) - float(ema21.iloc[-7])) / max(abs(float(ema21.iloc[-1])), 1e-9)
    six_candle_return = float(close.pct_change(6).iloc[-1]) if len(close) > 6 else 0.0
    bearish_ema_momentum = price < float(ema21.iloc[-1]) < float(ema55.iloc[-1]) and ema_slope < -0.001 and six_candle_return < 0
    bullish_ema_momentum = price > float(ema21.iloc[-1]) > float(ema55.iloc[-1]) and ema_slope > 0.001 and six_candle_return > 0
    bearish_structure_active = structural_support_break and lower_high_lower_low and bearish_ema_momentum
    bullish_structure_active = structural_resistance_reclaim and higher_high_higher_low and bullish_ema_momentum
    structure_reclaimed_bullish = price > recent_support + atr * 0.75 and price > float(ema21.iloc[-1]) and bullish_ema_momentum
    structure_reclaimed_bearish = price < recent_resistance - atr * 0.75 and price < float(ema21.iloc[-1]) and bearish_ema_momentum
    bias_flip_trigger = None
    if bearish_structure_active:
        bias_flip_trigger = "price broke recent structural support with LH/LL structure and bearish EMA/momentum confirmation"
    elif bullish_structure_active:
        bias_flip_trigger = "price reclaimed recent structural resistance with HH/HL structure and bullish EMA/momentum confirmation"

    if bullish_cycles >= 2:
        structure = "HH/HL"
    elif bearish_cycles >= 2:
        structure = "LH/LL"
    elif range_respected:
        structure = "Range"
    else:
        structure = "Mixed / Choppy"

    return {
        "structure": structure,
        "bullish_cycles": bullish_cycles,
        "bearish_cycles": bearish_cycles,
        "is_range": range_respected,
        "range_high": round(range_high, 6),
        "range_low": round(range_low, 6),
        "recent_support": round(recent_support, 6),
        "recent_resistance": round(recent_resistance, 6),
        "breakout_close": breakout_close,
        "breakdown_close": breakdown_close,
        "breakout_confirmed": breakout_close and (two_closes_up or retest_holds or continuation),
        "breakdown_confirmed": breakdown_close and (two_closes_down or retest_fails or continuation),
        "structural_support_break": structural_support_break,
        "structural_resistance_reclaim": structural_resistance_reclaim,
        "lower_high_lower_low": lower_high_lower_low,
        "higher_high_higher_low": higher_high_higher_low,
        "bearish_ema_momentum": bearish_ema_momentum,
        "bullish_ema_momentum": bullish_ema_momentum,
        "bearish_structure_active": bearish_structure_active,
        "bullish_structure_active": bullish_structure_active,
        "structure_reclaimed_bullish": structure_reclaimed_bullish,
        "structure_reclaimed_bearish": structure_reclaimed_bearish,
        "bias_flip_trigger": bias_flip_trigger,
        "volatility_compression": volatility_compression,
        "distance_from_range_boundary": round(distance_from_boundary, 3),
    }


def _regime_type(
    *,
    score: float,
    previous_delta: float,
    price: float,
    ema_fast: float,
    ema_slow: float,
    ema_slope: float,
    rsi: float,
    adx: float | None,
    breadth_above_ma_pct: float | None,
    structure_profile: dict[str, float | str | bool],
) -> MarketRegimeType:
    trend_confirmed = adx is not None and adx >= 22
    bullish_structure = price > ema_fast > ema_slow and ema_slope > 0.0015 and rsi >= 52
    bearish_structure = price < ema_fast < ema_slow and ema_slope < -0.0015 and rsi <= 48
    breadth_bullish = breadth_above_ma_pct is not None and breadth_above_ma_pct >= 62
    breadth_bearish = breadth_above_ma_pct is not None and breadth_above_ma_pct <= 38
    structure = str(structure_profile["structure"])
    bullish_cycles = int(structure_profile["bullish_cycles"])
    bearish_cycles = int(structure_profile["bearish_cycles"])
    breakout_close = bool(structure_profile["breakout_close"])
    breakdown_close = bool(structure_profile["breakdown_close"])
    breakout_confirmed = bool(structure_profile["breakout_confirmed"])
    breakdown_confirmed = bool(structure_profile["breakdown_confirmed"])
    bearish_structure_active = bool(structure_profile["bearish_structure_active"])
    bullish_structure_active = bool(structure_profile["bullish_structure_active"])

    if bearish_structure_active:
        return "BREAKDOWN" if bool(structure_profile["structural_support_break"]) else "TRENDING_DOWN"
    if bullish_structure_active:
        return "BREAKOUT" if bool(structure_profile["structural_resistance_reclaim"]) else "TRENDING_UP"
    if breakout_close and not breakout_confirmed:
        return "TRANSITION_TO_BULLISH"
    if breakdown_close and not breakdown_confirmed:
        return "TRANSITION_TO_BEARISH"
    if previous_delta >= 35 and score >= 15 and not (trend_confirmed and bullish_cycles >= 2):
        return "TRANSITION_TO_BULLISH"
    if previous_delta <= -35 and score <= -15 and not (trend_confirmed and bearish_cycles >= 2):
        return "TRANSITION_TO_BEARISH"
    if 15 <= score < 45 and (bullish_structure or breadth_bullish) and bullish_cycles < 2:
        return "TRANSITION_TO_BULLISH"
    if -45 < score <= -15 and (bearish_structure or breadth_bearish) and bearish_cycles < 2:
        return "TRANSITION_TO_BEARISH"

    if breakout_confirmed and score > 20:
        return "BREAKOUT"
    if breakdown_confirmed and score < -20:
        return "BREAKDOWN"
    if score >= 45 and bullish_structure and bullish_cycles >= 2:
        return "TRENDING_UP"
    if score <= -45 and bearish_structure and bearish_cycles >= 2:
        return "TRENDING_DOWN"
    if structure == "Range" and abs(score) <= 25 and (adx is None or adx < 22):
        return "RANGE_BOUND"
    if abs(score) <= 25 or structure == "Mixed / Choppy":
        return "CHOP"
    return "TRENDING_UP" if score > 0 else "TRENDING_DOWN"


def _confidence_breakdown(
    *,
    score: float,
    htf: str,
    ema_fast: float,
    ema_slow: float,
    ema_slope: float,
    rsi: float,
    adx: float | None,
    global_score: float | None,
    breadth_above_ma_pct: float | None,
    structure_profile: dict[str, float | str | bool],
) -> dict[str, float]:
    bullish_cycles = float(structure_profile["bullish_cycles"])
    bearish_cycles = float(structure_profile["bearish_cycles"])
    range_score = 0.0
    if bool(structure_profile["is_range"]):
        range_score += 45
    if bool(structure_profile["volatility_compression"]):
        range_score += 25
    if abs(score) <= 25:
        range_score += 20
    if adx is None or adx < 22:
        range_score += 10

    bullish = 0.0
    bearish = 0.0
    bullish += min(35.0, max(0.0, score) * 0.45)
    bearish += min(35.0, max(0.0, -score) * 0.45)
    bullish += min(25.0, bullish_cycles * 12.5)
    bearish += min(25.0, bearish_cycles * 12.5)
    bullish += 15.0 if ema_fast > ema_slow and ema_slope > 0 else 0.0
    bearish += 15.0 if ema_fast < ema_slow and ema_slope < 0 else 0.0
    bullish += 10.0 if rsi >= 55 else 0.0
    bearish += 10.0 if rsi <= 45 else 0.0
    bullish += 8.0 if htf == "HTF_BULLISH" else 0.0
    bearish += 8.0 if htf == "HTF_BEARISH" else 0.0
    bullish += 7.0 if global_score is not None and global_score > 10 else 0.0
    bearish += 7.0 if global_score is not None and global_score < -10 else 0.0
    bullish += 5.0 if breadth_above_ma_pct is not None and breadth_above_ma_pct >= 60 else 0.0
    bearish += 5.0 if breadth_above_ma_pct is not None and breadth_above_ma_pct <= 40 else 0.0
    return {
        "bullish": round(min(100.0, bullish), 1),
        "bearish": round(min(100.0, bearish), 1),
        "range": round(min(100.0, range_score), 1),
        "structure_consistency": round(max(bullish_cycles, bearish_cycles) * 20, 1),
        "breakout_confirmation": 25.0 if bool(structure_profile["breakout_confirmed"] or structure_profile["breakdown_confirmed"]) else 0.0,
        "volatility_compression": 20.0 if bool(structure_profile["volatility_compression"]) else 0.0,
        "distance_from_range_boundary": round((1 - float(structure_profile["distance_from_range_boundary"])) * 15, 1),
    }


def _trade_decision(regime_type: MarketRegimeType, confidence: float, score: float) -> str:
    if regime_type == "CHOP" or confidence < MIN_REGIME_CONFIDENCE:
        return "NO_TRADE"
    if regime_type in {"TRANSITION_TO_BULLISH", "TRANSITION_TO_BEARISH"}:
        return "WAIT"
    return "TRADE_ALLOWED"


def _bias_reason(regime_type: MarketRegimeType, label: str, score: float, structure_profile: dict[str, float | str | bool]) -> tuple[str, str | None]:
    trigger = structure_profile.get("bias_flip_trigger")
    if trigger:
        return str(trigger), str(trigger)
    if regime_type in {"TRENDING_DOWN", "BREAKDOWN", "TRANSITION_TO_BEARISH"}:
        return f"{label} bias from score {score:+.0f}, structure={structure_profile['structure']}, bearish_cycles={structure_profile['bearish_cycles']}.", None
    if regime_type in {"TRENDING_UP", "BREAKOUT", "TRANSITION_TO_BULLISH"}:
        return f"{label} bias from score {score:+.0f}, structure={structure_profile['structure']}, bullish_cycles={structure_profile['bullish_cycles']}.", None
    return f"{label} bias from mixed/range structure and score {score:+.0f}.", None


def regime_score_from_dataframe(df: pd.DataFrame, htf_dfs: list[pd.DataFrame] | None = None) -> float:
    if len(df) < 55:
        return 0.0
    close = df["close"].astype(float)
    price = float(close.iloc[-1])
    ema50 = _ema(close, 50)
    ema200 = _ema(close, 200) if len(close) >= 200 else _ema(close, min(100, len(close)))
    score = 0.0

    htf = higher_timeframe_bias(htf_dfs)
    if htf == "HTF_BULLISH":
        score += 25
    elif htf == "HTF_BEARISH":
        score -= 25
    else:
        trend = trend_direction(df)
        score += 15 if trend == "up" else -15 if trend == "down" else 0

    ema_fast = float(ema50.iloc[-1])
    ema_slow = float(ema200.iloc[-1])
    if price > ema_fast > ema_slow:
        score += 25
    elif price < ema_fast < ema_slow:
        score -= 25
    elif price > ema_fast:
        score += 10
    elif price < ema_fast:
        score -= 10

    lookback = min(12, len(ema50) - 1)
    slope = (ema_fast - float(ema50.iloc[-1 - lookback])) / max(abs(ema_fast), 1e-9)
    if slope > 0.003:
        score += 15
    elif slope < -0.003:
        score -= 15

    rsi = _rsi(close)
    if rsi >= 60:
        score += 15
    elif rsi > 50:
        score += 8
    elif rsi <= 40:
        score -= 15
    elif rsi < 50:
        score -= 8

    adx = _adx(df)
    if adx is not None and adx >= 22:
        if price >= ema_fast and slope >= 0:
            score += 10
        elif price <= ema_fast and slope <= 0:
            score -= 10

    return max(-100.0, min(100.0, round(score, 1)))


def detect_market_regime(
    df: pd.DataFrame,
    htf_dfs: list[pd.DataFrame] | None = None,
    *,
    global_score: float | None = None,
    breadth_above_ma_pct: float | None = None,
) -> MarketRegimeSnapshot:
    local_score = regime_score_from_dataframe(df, htf_dfs)
    score = local_score
    if global_score is not None:
        score += max(-10.0, min(10.0, global_score * 0.1))
    if breadth_above_ma_pct is not None:
        if breadth_above_ma_pct >= 65:
            score += 10
        elif breadth_above_ma_pct <= 35:
            score -= 10
    score = max(-100.0, min(100.0, round(score, 1)))
    close = df["close"].astype(float)
    price = float(close.iloc[-1])
    ema50 = _ema(close, 50)
    ema200 = _ema(close, 200) if len(close) >= 200 else _ema(close, min(100, len(close)))
    rsi = round(_rsi(close), 1)
    adx = _adx(df)
    htf = higher_timeframe_bias(htf_dfs)
    ema_fast = float(ema50.iloc[-1])
    ema_slow = float(ema200.iloc[-1])
    ema_slope = (ema_fast - float(ema50.iloc[max(0, len(ema50) - 13)])) / max(abs(ema_fast), 1e-9)
    score_delta = _recent_score_slope(df, htf_dfs)
    structure_profile = _structure_profile(df)
    regime_type = _regime_type(
        score=score,
        previous_delta=score_delta,
        price=price,
        ema_fast=ema_fast,
        ema_slow=ema_slow,
        ema_slope=ema_slope,
        rsi=rsi,
        adx=adx,
        breadth_above_ma_pct=breadth_above_ma_pct,
        structure_profile=structure_profile,
    )
    label = {
        "RANGE_BOUND": "Range Bound",
        "TRENDING_UP": "Trending Up",
        "TRENDING_DOWN": "Trending Down",
        "BREAKOUT": "Breakout",
        "BREAKDOWN": "Breakdown",
        "CHOP": "Chop / No Trade",
        "TRANSITION_TO_BULLISH": "Transition to Bullish",
        "TRANSITION_TO_BEARISH": "Transition to Bearish",
    }[regime_type]
    if regime_type in {"TRENDING_UP", "TRENDING_DOWN"}:
        label = _label(score)
    confidence_breakdown = _confidence_breakdown(
        score=score,
        htf=htf,
        ema_fast=ema_fast,
        ema_slow=ema_slow,
        ema_slope=ema_slope,
        rsi=rsi,
        adx=adx,
        global_score=global_score,
        breadth_above_ma_pct=breadth_above_ma_pct,
        structure_profile=structure_profile,
    )
    confidence_score = round(max(confidence_breakdown["bullish"], confidence_breakdown["bearish"], confidence_breakdown["range"]), 1)
    trade_decision = _trade_decision(regime_type, confidence_score, score)
    bias, long_bias, short_bias = _bias(label, regime_type)
    bias_reason, bias_flip_trigger = _bias_reason(regime_type, label, score, structure_profile)
    components = {
        "local_score": local_score,
        "score_delta_12_candles": score_delta,
        "global_score": global_score,
        "breadth_above_ma_pct": breadth_above_ma_pct,
        "higher_timeframe_bias": htf,
        "ema_50": round(ema_fast, 6),
        "ema_200": round(ema_slow, 6),
        "ema_slope": round(ema_slope, 5),
        "rsi": rsi,
        "adx": adx,
        **structure_profile,
    }
    explanation = (
        f"{label} ({score:+.0f}, confidence {confidence_score:.0f}) from HTF={htf}, EMA structure, "
        f"EMA slope, RSI={rsi}, ADX={adx or '-'}, score delta={score_delta:+.0f}. "
        f"Decision: {trade_decision}. Bias: {bias}. Reason: {bias_reason}."
    )
    if global_score is not None:
        explanation += f" BTC/ETH global bias contributes {global_score:+.0f}."
    if breadth_above_ma_pct is not None:
        explanation += f" Breadth above key MAs is {breadth_above_ma_pct:.0f}%."
    logger.info(
        "Market regime bias=%s reason=%s flip_trigger=%s regime=%s score=%s confidence=%s decision=%s",
        bias,
        bias_reason,
        bias_flip_trigger,
        regime_type,
        score,
        confidence_score,
        trade_decision,
    )
    return MarketRegimeSnapshot(
        score=score,
        label=label,
        regime_type=regime_type,
        confidence_score=confidence_score,
        confidence_breakdown=confidence_breakdown,
        structure=str(structure_profile["structure"]),
        is_transition=regime_type in {"TRANSITION_TO_BULLISH", "TRANSITION_TO_BEARISH"},
        trade_decision=trade_decision,
        bias=bias,
        long_bias=long_bias,
        short_bias=short_bias,
        bias_reason=bias_reason,
        bias_flip_trigger=bias_flip_trigger,
        updated_at=datetime.now(UTC).replace(microsecond=0),
        components=components,
        explanation=explanation,
    )
