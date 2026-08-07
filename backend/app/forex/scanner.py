from __future__ import annotations

from datetime import UTC, datetime, timedelta
from collections import Counter
import hashlib
import logging
from uuid import uuid4

import pandas as pd

from app.config import get_settings
from app.forex.config import (
    STRATEGY_FAMILY,
    STRATEGY_VERSION,
    SUPPORTED_FOREX_PAIRS,
    TIMEFRAME_EXPIRY_HOURS,
    ForexPairConfig,
    normalize_forex_timeframe,
)
from app.forex.models import ForexPairInfo, ForexScanRunResult, ForexSignalPlan
from app.forex.market_data import ForexMarketDataService
from app.forex.news import forex_news_risk
from app.forex.providers import (
    ForexDataProvider,
    ForexProviderError,
    ForexProviderNotConfigured,
    ForexProviderQuotaExceeded,
    get_forex_provider,
)
from app.forex.sessions import forex_session_state
from app.forex.storage import (
    find_active_by_dedupe,
    find_waiting_retest,
    finish_scan_run,
    get_candle_evaluation,
    get_signal,
    insert_signal,
    promote_retest_signal,
    save_candle_evaluation,
    start_scan_run,
    update_signal_market_state,
)
from app.forex.telegram import enqueue_forex_signal

logger = logging.getLogger(__name__)


def _ema(series: pd.Series, span: int) -> float:
    if series.empty:
        return 0.0
    return float(series.ewm(span=min(span, len(series)), adjust=False).mean().iloc[-1])


def _trend(df: pd.DataFrame) -> str:
    if len(df) < 25:
        return "neutral"
    close = df["close"].astype(float)
    price = float(close.iloc[-1])
    fast = _ema(close, 20)
    slow = _ema(close, 50)
    previous_fast = _ema(close.iloc[:-5], 20)
    if price > fast > slow and fast >= previous_fast:
        return "bullish"
    if price < fast < slow and fast <= previous_fast:
        return "bearish"
    return "neutral"


def _structure(df: pd.DataFrame) -> tuple[str, str]:
    if len(df) < 60:
        return "neutral", "insufficient-structure"
    recent = df.tail(30)
    previous = df.iloc[-60:-30]
    recent_high, recent_low = float(recent["high"].max()), float(recent["low"].min())
    previous_high, previous_low = float(previous["high"].max()), float(previous["low"].min())
    if recent_high > previous_high and recent_low > previous_low:
        structure = "bullish"
    elif recent_high < previous_high and recent_low < previous_low:
        structure = "bearish"
    else:
        structure = "neutral"
    candle_time = pd.Timestamp(recent.iloc[-1]["timestamp"]).isoformat()
    return structure, f"{candle_time}:{structure}"


HIGHER_TIMEFRAME = {"15M": "1H", "1H": "4H", "4H": "1D", "1D": None}


def _entry_confirmation(
    df: pd.DataFrame,
    direction: str,
    timeframe: str,
) -> tuple[bool, str, str, float | None]:
    if len(df) < 12:
        return False, f"{timeframe} history is insufficient.", "none", None
    recent = df.tail(12)
    last = recent.iloc[-1]
    prior = recent.iloc[:-1]
    earlier = recent.iloc[:-2]
    close, open_ = float(last["close"]), float(last["open"])
    high, low = float(last["high"]), float(last["low"])
    prior_high, prior_low = float(prior["high"].max()), float(prior["low"].min())
    breakout_high = float(earlier["high"].max())
    breakout_low = float(earlier["low"].min())
    atr = max(_atr(df), 1e-9)
    body = abs(close - open_)
    previous = prior.iloc[-1]
    previous_close, previous_open = float(previous["close"]), float(previous["open"])
    previous_body = abs(previous_close - previous_open)
    if direction == "LONG":
        if close > breakout_high:
            displaced = previous_close > breakout_high and previous_body >= atr * 0.65
            follow_through = close > open_ and body >= atr * 0.2 and close >= previous_close - atr * 0.1
            confirmed = displaced and follow_through
            return confirmed, (
                f"{timeframe} bullish breakout has displacement and follow-through."
                if confirmed
                else f"{timeframe} bullish breakout has meaningful displacement and needs a later retest or follow-through candle."
                if close > open_ and body >= atr * 0.65
                else f"{timeframe} bullish breakout lacks meaningful displacement."
            ), "breakout", breakout_high
        if low <= prior_low and close > open_:
            rejection = close >= low + (high - low) * 0.6 and body >= atr * 0.25
            return rejection, f"{timeframe} downside liquidity sweep closed with bullish rejection.", "sweep", prior_low
        ema20 = _ema(df["close"].astype(float).iloc[:-1], 20)
        pullback = float(previous["low"]) <= ema20 + atr * 0.3 and float(previous["close"]) <= float(previous["open"])
        continuation = pullback and close > open_ and body >= atr * 0.3
        return continuation, (
            f"{timeframe} bullish pullback held and continuation closed higher."
            if continuation else f"{timeframe} bullish continuation needs a completed pullback/retest."
        ), "continuation", ema20
    else:
        if close < breakout_low:
            displaced = previous_close < breakout_low and previous_body >= atr * 0.65
            follow_through = close < open_ and body >= atr * 0.2 and close <= previous_close + atr * 0.1
            confirmed = displaced and follow_through
            return confirmed, (
                f"{timeframe} bearish breakdown has displacement and follow-through."
                if confirmed
                else f"{timeframe} bearish breakdown has meaningful displacement and needs a later retest or follow-through candle."
                if close < open_ and body >= atr * 0.65
                else f"{timeframe} bearish breakdown lacks meaningful displacement."
            ), "breakout", breakout_low
        if high >= prior_high and close < open_:
            rejection = close <= high - (high - low) * 0.6 and body >= atr * 0.25
            return rejection, f"{timeframe} upside liquidity sweep closed with bearish rejection.", "sweep", prior_high
        ema20 = _ema(df["close"].astype(float).iloc[:-1], 20)
        pullback = float(previous["high"]) >= ema20 - atr * 0.3 and float(previous["close"]) >= float(previous["open"])
        continuation = pullback and close < open_ and body >= atr * 0.3
        return continuation, (
            f"{timeframe} bearish pullback held and continuation closed lower."
            if continuation else f"{timeframe} bearish continuation needs a completed pullback/retest."
        ), "continuation", ema20


def _atr(df: pd.DataFrame) -> float:
    high, low, close = (df[column].astype(float) for column in ("high", "low", "close"))
    ranges = pd.concat(
        [high - low, (high - close.shift()).abs(), (low - close.shift()).abs()],
        axis=1,
    ).max(axis=1)
    return float(ranges.tail(14).mean())


def _rsi(close: pd.Series, period: int = 14) -> float:
    delta = close.astype(float).diff()
    gains = delta.clip(lower=0).rolling(period).mean()
    losses = (-delta.clip(upper=0)).rolling(period).mean()
    denominator = float(losses.iloc[-1])
    if pd.isna(denominator):
        return 50.0
    if denominator == 0:
        return 100.0
    relative_strength = float(gains.iloc[-1]) / denominator
    return 100 - (100 / (1 + relative_strength))


def _rr(entry: float, stop: float, target: float) -> float:
    risk = abs(entry - stop)
    return round(abs(target - entry) / risk, 2) if risk else 0.0


def _grade(score: float) -> str:
    return "A+" if score >= 90 else "A" if score >= 80 else "B"


def _trend_score(df: pd.DataFrame, direction: str, structure: str, htf_bias: str) -> float:
    close = df["close"].astype(float)
    fast, slow = _ema(close, 20), _ema(close, 50)
    previous_fast = _ema(close.iloc[:-5], 20)
    bullish = direction == "LONG"
    score = 0.0
    if (bullish and float(close.iloc[-1]) > fast > slow) or (not bullish and float(close.iloc[-1]) < fast < slow):
        score += 35
    if (bullish and fast > previous_fast) or (not bullish and fast < previous_fast):
        score += 15
    if structure == ("bullish" if bullish else "bearish"):
        score += 25
    if htf_bias == ("bullish" if bullish else "bearish"):
        score += 25
    elif htf_bias == "not-applicable":
        score += 15
    return min(100.0, score)


def _consecutive_strong_candles(df: pd.DataFrame, direction: str, atr: float) -> int:
    count = 0
    for _, candle in df.tail(6).iloc[::-1].iterrows():
        body = abs(float(candle["close"]) - float(candle["open"]))
        directional = float(candle["close"]) > float(candle["open"]) if direction == "LONG" else float(candle["close"]) < float(candle["open"])
        if directional and body >= atr * 0.45:
            count += 1
        else:
            break
    return count


def _swing_levels(df: pd.DataFrame, direction: str, price: float) -> list[float]:
    recent = df.tail(90).iloc[:-1].reset_index(drop=True)
    levels: list[float] = []
    for index in range(2, len(recent) - 2):
        window = recent.iloc[index - 2:index + 3]
        if direction == "LONG":
            value = float(recent.iloc[index]["high"])
            if value == float(window["high"].max()) and value > price:
                levels.append(value)
        else:
            value = float(recent.iloc[index]["low"])
            if value == float(window["low"].min()) and value < price:
                levels.append(value)
    return sorted(set(levels)) if direction == "LONG" else sorted(set(levels), reverse=True)


def _latest_directional_swing(df: pd.DataFrame, direction: str) -> float:
    recent = df.tail(40).reset_index(drop=True)
    candidates: list[float] = []
    for index in range(2, len(recent) - 2):
        window = recent.iloc[index - 2:index + 3]
        if direction == "LONG":
            value = float(recent.iloc[index]["low"])
            if value == float(window["low"].min()):
                candidates.append(value)
        else:
            value = float(recent.iloc[index]["high"])
            if value == float(window["high"].max()):
                candidates.append(value)
    if candidates:
        return candidates[-1]
    return float(recent["low"].tail(12).min()) if direction == "LONG" else float(recent["high"].tail(12).max())


def _entry_quality(
    df: pd.DataFrame,
    direction: str,
    *,
    entry_ok: bool,
    trigger_type: str,
    retest_level: float | None,
) -> dict[str, object]:
    close = df["close"].astype(float)
    price = float(close.iloc[-1])
    atr = max(_atr(df), 1e-9)
    ema20 = _ema(close, 20)
    recent = df.tail(30)
    recent_high, recent_low = float(recent["high"].max()), float(recent["low"].min())
    width = max(recent_high - recent_low, atr)
    position = (price - recent_low) / width
    swing_origin = _latest_directional_swing(df, direction)
    swing_extension = (price - swing_origin) / atr if direction == "LONG" else (swing_origin - price) / atr
    ema_extension = abs(price - ema20) / atr
    consecutive = _consecutive_strong_candles(df, direction, atr)
    levels = _swing_levels(df, direction, price)
    opposing_level = levels[0] if levels else None
    reasons: list[str] = []
    gates: list[str] = []
    hard_gate = False
    score = 100.0

    if not entry_ok:
        hard_gate = True
        score -= 30
        reasons.append("entry trigger is not confirmed")
        gates.append("ENTRY_TRIGGER_NOT_CONFIRMED")
    if ema_extension > 1.5 or swing_extension > 3.0:
        hard_gate = True
        score -= 25
        reasons.append(f"price is overextended ({ema_extension:.2f} ATR from EMA20; {swing_extension:.2f} ATR from swing)")
        gates.append("OVEREXTENDED")
    if consecutive >= 3:
        hard_gate = True
        score -= 20
        reasons.append(f"{consecutive} strong directional candles have already printed")
        gates.append("CONSECUTIVE_CANDLES")
    near_end = position >= 0.85 if direction == "LONG" else position <= 0.15
    if near_end:
        hard_gate = True
        score -= 20
        reasons.append("price is near the exhausted end of its recent range")
        gates.append("RANGE_POSITION_FAILED")
    if trigger_type == "breakout" and not entry_ok:
        reasons.append("single-close breakout requires later follow-through or retest")

    return {
        "score": max(0.0, score),
        "hard_gate": hard_gate,
        "reasons": reasons,
        "gates": gates,
        "atr": atr,
        "ema20": ema20,
        "swing_extension": swing_extension,
        "opposing_level": opposing_level,
        "retest_level": retest_level if retest_level is not None else ema20,
    }


def _retest_confirmed(df: pd.DataFrame, direction: str, level: float, setup_candle_time: datetime | None) -> bool:
    if setup_candle_time is None or df.empty:
        return False
    last = df.iloc[-1]
    candle_time = pd.Timestamp(last["timestamp"]).to_pydatetime()
    if candle_time <= setup_candle_time:
        return False
    atr = max(_atr(df), 1e-9)
    open_, high, low, close = (float(last[key]) for key in ("open", "high", "low", "close"))
    if direction == "LONG":
        return low <= level + atr * 0.25 and close >= level and close > open_
    return high >= level - atr * 0.25 and close <= level and close < open_


def _pending_retest_confirmed(
    df: pd.DataFrame,
    direction: str,
    pending_retest: ForexSignalPlan,
) -> bool:
    level = pending_retest.retest_level
    return bool(
        level is not None
        and _retest_confirmed(df, direction, level, pending_retest.setup_candle_time)
    )


def _stored_breakout_displacement_valid(
    df: pd.DataFrame,
    direction: str,
    pending_retest: ForexSignalPlan,
) -> bool:
    """Revalidate original breakout quality separately from retest occurrence."""
    trigger = pending_retest.entry_trigger.lower()
    if "breakout" not in trigger and "breakdown" not in trigger:
        return True
    setup_time = pending_retest.setup_candle_time
    if setup_time is None:
        return False
    timestamps = pd.to_datetime(df["timestamp"], utc=True)
    matches = df.loc[timestamps == pd.Timestamp(setup_time)]
    if matches.empty:
        return False
    setup = matches.iloc[-1]
    setup_body = abs(float(setup["close"]) - float(setup["open"]))
    directional = (
        float(setup["close"]) > float(setup["open"])
        if direction == "LONG"
        else float(setup["close"]) < float(setup["open"])
    )
    history = df.loc[timestamps <= pd.Timestamp(setup_time)]
    return directional and setup_body >= max(_atr(history), 1e-9) * 0.65


def analyze_forex_timeframe(
    pair: ForexPairConfig,
    candles: pd.DataFrame,
    *,
    htf_candles: pd.DataFrame | None = None,
    pending_retest: ForexSignalPlan | None = None,
    timeframe: str,
    scan_id: str,
    session_label: str,
    news_risk: str,
    now: datetime,
) -> tuple[dict | None, dict[str, object]]:
    timeframe = normalize_forex_timeframe(timeframe)
    if len(candles) < 60:
        return None, {"symbol": pair.pair, "decision": "NO_TRADE", "reason": f"Insufficient {timeframe} candle history."}

    last_candle = candles.iloc[-1]
    confirmation_ohlc = {
        "timestamp": pd.Timestamp(last_candle["timestamp"]).isoformat(),
        "open": float(last_candle["open"]),
        "high": float(last_candle["high"]),
        "low": float(last_candle["low"]),
        "close": float(last_candle["close"]),
    }
    retest_confirmed: bool | None = None

    def audit_result(
        decision: str,
        reason: str,
        *,
        gate: str | None = None,
        entry_quality_passed: bool | None = None,
        score: float | None = None,
    ) -> dict[str, object]:
        result: dict[str, object] = {
            "symbol": pair.pair,
            "decision": decision,
            "reason": reason,
        }
        if score is not None:
            result["score"] = score
        if pending_retest:
            result.update({
                "retest_signal_id": pending_retest.id,
                "retest_confirmed": bool(retest_confirmed),
                "retest_level": pending_retest.retest_level,
                "confirmation_candle_ohlc": confirmation_ohlc,
                "entry_quality_passed": entry_quality_passed,
                "failing_gate": gate,
                "promotion_occurred": False,
            })
        return result

    if pending_retest:
        retest_confirmed = _pending_retest_confirmed(
            candles, pending_retest.direction, pending_retest
        )
        if not retest_confirmed:
            return None, audit_result(
                "WAIT_FOR_RETEST",
                "RETEST_NOT_CONFIRMED: the latest completed candle did not satisfy the stored retest condition.",
                gate="RETEST_NOT_CONFIRMED",
                entry_quality_passed=False,
            )
        if not _stored_breakout_displacement_valid(
            candles, pending_retest.direction, pending_retest
        ):
            return None, audit_result(
                "WAIT_FOR_RETEST",
                "RETEST_CONFIRMED_STRUCTURE_FAILED: the original breakout candle lacked meaningful displacement.",
                gate="RETEST_CONFIRMED_STRUCTURE_FAILED",
                entry_quality_passed=False,
            )

    if news_risk == "HIGH":
        reason = "High-impact news risk."
        if pending_retest:
            reason = f"RETEST_CONFIRMED_NEWS_RISK: {reason}"
        return None, audit_result(
            "WAIT_FOR_RETEST" if pending_retest else "NO_TRADE",
            reason,
            gate="RETEST_CONFIRMED_NEWS_RISK" if pending_retest else None,
            entry_quality_passed=False if pending_retest else None,
        )

    regime = _trend(candles)
    structure, structure_id = _structure(candles)
    expected_regime = (
        "bullish" if pending_retest and pending_retest.direction == "LONG"
        else "bearish" if pending_retest
        else regime
    )
    if (
        regime not in {"bullish", "bearish"}
        or structure != regime
        or (pending_retest and regime != expected_regime)
    ):
        reason = f"{timeframe} regime={regime}, structure={structure}."
        if pending_retest:
            reason = f"RETEST_CONFIRMED_STRUCTURE_FAILED: {reason}"
        return None, audit_result(
            "WAIT_FOR_RETEST" if pending_retest else "NO_TRADE",
            reason,
            gate="RETEST_CONFIRMED_STRUCTURE_FAILED" if pending_retest else None,
            entry_quality_passed=False if pending_retest else None,
        )

    direction = pending_retest.direction if pending_retest else ("LONG" if regime == "bullish" else "SHORT")
    required_htf = HIGHER_TIMEFRAME[timeframe]
    htf_bias = _trend(htf_candles) if required_htf and htf_candles is not None else "not-applicable"
    # A neutral higher timeframe is non-confirming, but it is not opposing.
    # It receives no HTF trend-score credit and must still clear every
    # downstream entry-quality gate and the unchanged minimum setup score.
    if required_htf and htf_bias not in {regime, "neutral"}:
        reason = f"{timeframe} {regime} setup conflicts with {required_htf} bias={htf_bias}."
        if pending_retest:
            reason = f"RETEST_CONFIRMED_HTF_ALIGNMENT_FAILED: {reason}"
        return None, audit_result(
            "WAIT_FOR_RETEST" if pending_retest else "NO_TRADE",
            reason,
            gate="RETEST_CONFIRMED_HTF_ALIGNMENT_FAILED" if pending_retest else None,
            entry_quality_passed=False if pending_retest else None,
        )

    if pending_retest:
        entry_ok = True
        trigger_type = "retest"
        trigger_level = pending_retest.retest_level
        entry_trigger = (
            f"{timeframe} later completed candle retested {pending_retest.retest_level:g}, "
            f"held, and closed {direction.lower()}."
        )
    else:
        entry_ok, entry_trigger, trigger_type, trigger_level = _entry_confirmation(
            candles, direction, timeframe
        )

    current = float(candles["close"].iloc[-1])
    quality = _entry_quality(
        candles,
        direction,
        entry_ok=entry_ok,
        trigger_type=trigger_type,
        retest_level=trigger_level,
    )
    timeframe_atr = max(float(quality["atr"]), pair.pip_size * 4)
    entry_anchor = (
        pending_retest.entry_price
        if pending_retest
        else float(quality["retest_level"]) if quality["hard_gate"] else current
    )
    half_zone = max(timeframe_atr * 0.12, pair.pip_size)
    if pending_retest:
        entry_low, entry_high = pending_retest.entry_low, pending_retest.entry_high
        stop = pending_retest.stop_loss
        stop_distance = abs(entry_anchor - stop)
    else:
        entry_low, entry_high = entry_anchor - half_zone, entry_anchor + half_zone
        recent_structure = candles.tail(12)
        if direction == "LONG":
            stop = float(recent_structure["low"].min()) - timeframe_atr * 0.08
            stop_distance = entry_anchor - stop
        else:
            stop = float(recent_structure["high"].max()) + timeframe_atr * 0.08
            stop_distance = stop - entry_anchor

    if stop_distance <= 0:
        reason = "Retest level does not leave a valid structural stop."
        if pending_retest:
            reason = f"RETEST_CONFIRMED_STRUCTURE_FAILED: {reason}"
        return None, audit_result(
            "WAIT_FOR_RETEST" if pending_retest else "NO_TRADE",
            reason,
            gate="RETEST_CONFIRMED_STRUCTURE_FAILED" if pending_retest else None,
            entry_quality_passed=False if pending_retest else None,
        )

    opposing_level = quality["opposing_level"]
    if opposing_level is None:
        clear_space = stop_distance * 3.0
    elif direction == "LONG":
        clear_space = float(opposing_level) - entry_anchor
    else:
        clear_space = entry_anchor - float(opposing_level)
    clear_space_r = clear_space / max(stop_distance, 1e-9)
    if opposing_level is not None and clear_space_r < 1.0:
        quality["hard_gate"] = True
        quality["score"] = max(0.0, float(quality["score"]) - 35)
        quality["reasons"].append(
            f"opposing structure at {float(opposing_level):g} leaves only {clear_space_r:.2f}R clear space before TP1"
        )
        quality["gates"].append("TARGET_SPACE_FAILED")
    tp1_r = min(1.5, clear_space_r * 0.75)
    tp2_r = min(2.4, clear_space_r * 0.95)
    if tp1_r < 1.0 or tp2_r <= tp1_r:
        quality["hard_gate"] = True
        quality["score"] = max(0.0, float(quality["score"]) - 20)
        quality["reasons"].append("structure does not provide valid room for both targets")
        if "TARGET_SPACE_FAILED" not in quality["gates"]:
            quality["gates"].append("TARGET_SPACE_FAILED")
    tp1_r = max(tp1_r, 1.0)
    tp2_r = max(tp2_r, 1.1)
    if pending_retest:
        tp1 = pending_retest.take_profit_1
        tp2 = pending_retest.take_profit_2
    elif direction == "LONG":
        tp1 = entry_anchor + stop_distance * tp1_r
        tp2 = entry_anchor + stop_distance * tp2_r
    else:
        tp1 = entry_anchor - stop_distance * tp1_r
        tp2 = entry_anchor - stop_distance * tp2_r

    settings = get_settings()
    stop_pips = stop_distance / pair.pip_size
    timeframe_stop_multiplier = {"15M": 1.0, "1H": 1.0, "4H": 2.5, "1D": 6.0}[timeframe]
    maximum_stop = min(
        settings.forex_max_stop_pips,
        pair.max_atr_pips_1h * timeframe_stop_multiplier,
    )
    if stop_pips < settings.forex_min_stop_pips or stop_pips > maximum_stop:
        reason = f"Structural stop distance {stop_pips:.1f} pips is outside risk limits."
        stop_gate = (
            "RETEST_CONFIRMED_STOP_TOO_NARROW"
            if stop_pips < settings.forex_min_stop_pips
            else "RETEST_CONFIRMED_STOP_TOO_WIDE"
        )
        if pending_retest:
            reason = f"{stop_gate}: {reason}"
        return None, audit_result(
            "WAIT_FOR_RETEST" if pending_retest else "NO_TRADE",
            reason,
            gate=stop_gate if pending_retest else None,
            entry_quality_passed=False if pending_retest else None,
        )

    trend_score = _trend_score(candles, direction, structure, htf_bias)
    entry_quality_score = float(quality["score"])
    session_adjustment = 3.0 if session_label in pair.relevant_sessions else 0.0
    news_adjustment = 0.0 if news_risk == "LOW" else -5.0
    technical_score = max(0.0, min(100.0, round(
        trend_score * 0.45 + entry_quality_score * 0.55 + session_adjustment + news_adjustment,
        1,
    )))
    waiting = bool(quality["hard_gate"])
    if not waiting and technical_score < 70:
        reason = f"Setup score {technical_score:.1f} is below 70."
        if pending_retest:
            reason = f"RETEST_CONFIRMED_SETUP_SCORE_FAILED: {reason}"
        return None, audit_result(
            "WAIT_FOR_RETEST" if pending_retest else "NO_TRADE",
            reason,
            gate="RETEST_CONFIRMED_SETUP_SCORE_FAILED" if pending_retest else None,
            entry_quality_passed=False if pending_retest else None,
            score=technical_score,
        )

    strategy_family = f"{STRATEGY_FAMILY}_{timeframe.lower()}"
    # Active consecutive candles in the same directional structure are one setup,
    # while per-candle evaluation is tracked separately.
    raw_key = "|".join(
        [pair.pair, timeframe, direction, strategy_family, STRATEGY_VERSION, structure]
    )
    dedupe_key = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
    precision = 3 if pair.pip_size >= 0.01 else 5
    quality_gate = str(quality["gates"][0]) if quality["gates"] else None
    retest_failure_code = (
        f"RETEST_CONFIRMED_{quality_gate}" if pending_retest and quality_gate else None
    )
    state_label = (
        "retest confirmed but final approval blocked"
        if pending_retest and waiting
        else "waiting for retest" if waiting
        else "approved"
    )
    plan = {
        "id": str(uuid4()),
        "symbol": pair.pair,
        "timeframe": timeframe,
        "direction": direction,
        "entry_type": "ZONE",
        "entry_price": round(entry_anchor, precision),
        "entry_low": round(entry_low, precision),
        "entry_high": round(entry_high, precision),
        "stop_loss": round(stop, precision),
        "take_profit_1": round(tp1, precision),
        "take_profit_2": round(tp2, precision),
        "tp1_closes_position": get_settings().forex_close_entire_position_at_tp1,
        "risk_reward_1": _rr(entry_anchor, stop, tp1),
        "risk_reward_2": _rr(entry_anchor, stop, tp2),
        "execution_timeframe": timeframe.lower(),
        "setup_timeframe": timeframe.lower(),
        "bias_timeframe": required_htf.lower() if required_htf else timeframe.lower(),
        "timeframe_alignment": (
            f"{timeframe} setup aligned with {required_htf} bias"
            if required_htf and htf_bias == regime
            else f"{timeframe} setup allowed under neutral {required_htf} bias"
            if required_htf and htf_bias == "neutral"
            else f"{timeframe} primary structural bias"
        ),
        "htf_bias": htf_bias.upper(),
        "setup_structure": f"{structure.upper()}:{structure_id}",
        "entry_trigger": entry_trigger,
        "market_session": session_label,
        "setup_score": technical_score,
        "trend_score": trend_score,
        "entry_quality_score": entry_quality_score,
        "technical_score": technical_score,
        "strategy_family": strategy_family,
        "strategy_version": STRATEGY_VERSION,
        "market_regime": f"{regime.title()} {timeframe} trend",
        "bias": regime.upper(),
        "setup_reason": (
            f"{retest_failure_code + ': ' if retest_failure_code else ''}"
            f"{pair.pair} {state_label} on {timeframe}. "
            f"Trend score={trend_score:.1f}; entry-quality score={entry_quality_score:.1f}; "
            f"final technical score={technical_score:.1f}. {entry_trigger} "
            f"{'; '.join(quality['reasons']) if quality['reasons'] else 'Entry-quality gates passed.'} "
            f"RSI context={_rsi(candles['close']):.1f} (informational only)."
        ),
        "status": "WAIT_FOR_RETEST" if waiting else "PENDING_ENTRY",
        "created_at": now,
        "expires_at": now + timedelta(hours=TIMEFRAME_EXPIRY_HOURS[timeframe]),
        "source_scan_id": scan_id,
        "dedupe_key": dedupe_key,
        "grade": "WAIT" if waiting else _grade(technical_score),
        "news_risk": news_risk,
        "spread_status": "SAFE",
        "retest_level": round(float(quality["retest_level"]), precision),
        "setup_candle_time": pd.Timestamp(candles.iloc[-1]["timestamp"]).to_pydatetime(),
        "retest_confirmed_at": (
            pd.Timestamp(candles.iloc[-1]["timestamp"]).to_pydatetime()
            if pending_retest and entry_ok else None
        ),
    }
    if pending_retest:
        # Promotion keeps the original trade identity and execution plan. Only
        # confirmation/scoring/context metadata may change after reevaluation.
        plan.update({
            "id": pending_retest.id,
            "direction": pending_retest.direction,
            "entry_type": pending_retest.entry_type,
            "entry_price": pending_retest.entry_price,
            "entry_low": pending_retest.entry_low,
            "entry_high": pending_retest.entry_high,
            "stop_loss": pending_retest.stop_loss,
            "take_profit_1": pending_retest.take_profit_1,
            "take_profit_2": pending_retest.take_profit_2,
            "tp1_closes_position": pending_retest.tp1_closes_position,
            "risk_reward_1": pending_retest.risk_reward_1,
            "risk_reward_2": pending_retest.risk_reward_2,
            "execution_timeframe": pending_retest.execution_timeframe,
            "setup_timeframe": pending_retest.setup_timeframe,
            "bias_timeframe": pending_retest.bias_timeframe,
            "strategy_family": pending_retest.strategy_family,
            "strategy_version": pending_retest.strategy_version,
            "setup_structure": pending_retest.setup_structure,
            "market_regime": pending_retest.market_regime,
            "bias": pending_retest.bias,
            "created_at": pending_retest.created_at,
            "expires_at": pending_retest.expires_at,
            "source_scan_id": pending_retest.source_scan_id,
            "dedupe_key": pending_retest.dedupe_key,
            "retest_level": pending_retest.retest_level,
            "setup_candle_time": pending_retest.setup_candle_time,
        })
    decision = "WAIT_FOR_RETEST" if waiting else "TRADE"
    return plan, audit_result(
        decision,
        plan["setup_reason"],
        gate=retest_failure_code,
        entry_quality_passed=not waiting,
        score=technical_score,
    )


def _save_scan_evaluation(
    *,
    evaluation_key: str,
    symbol: str,
    timeframe: str,
    candle_open_at: str,
    audit: dict[str, object],
    signal_id: str | None = None,
    promotion_occurred: bool = False,
) -> None:
    retest_signal_id = audit.get("retest_signal_id")
    if retest_signal_id:
        logger.info(
            "Forex retest reevaluated signal_id=%s confirmed=%s level=%s candle=%s "
            "entry_quality_passed=%s failing_gate=%s promoted=%s",
            retest_signal_id,
            audit.get("retest_confirmed"),
            audit.get("retest_level"),
            audit.get("confirmation_candle_ohlc"),
            audit.get("entry_quality_passed"),
            audit.get("failing_gate"),
            promotion_occurred,
        )
    save_candle_evaluation(
        evaluation_key=evaluation_key,
        symbol=symbol,
        timeframe=timeframe,
        candle_open_at=candle_open_at,
        strategy_family=STRATEGY_FAMILY,
        strategy_version=STRATEGY_VERSION,
        decision=str(audit.get("decision") or "NO_TRADE"),
        reason=str(audit.get("reason") or "No valid setup."),
        signal_id=signal_id,
        retest_signal_id=str(retest_signal_id) if retest_signal_id else None,
        retest_confirmed=(
            bool(audit["retest_confirmed"])
            if audit.get("retest_confirmed") is not None else None
        ),
        retest_level=(
            float(audit["retest_level"])
            if audit.get("retest_level") is not None else None
        ),
        confirmation_candle_ohlc=(
            audit.get("confirmation_candle_ohlc")
            if isinstance(audit.get("confirmation_candle_ohlc"), dict) else None
        ),
        entry_quality_passed=(
            bool(audit["entry_quality_passed"])
            if audit.get("entry_quality_passed") is not None else None
        ),
        failing_gate=(str(audit["failing_gate"]) if audit.get("failing_gate") else None),
        promotion_occurred=promotion_occurred,
    )


async def scan_forex(
    provider: ForexDataProvider | None = None,
    timeframe: str = "1H",
    trigger_source: str = "scheduled",
) -> ForexScanRunResult:
    timeframe = normalize_forex_timeframe(timeframe)
    now = datetime.now(UTC).replace(microsecond=0)
    scan_id = str(uuid4())
    market_data = ForexMarketDataService(provider or get_forex_provider())
    provider_name = market_data.provider.name
    if trigger_source not in {"scheduled", "manual"}:
        raise ValueError(f"Unsupported Forex scan trigger: {trigger_source}")
    start_scan_run(scan_id, provider_name, now, timeframe, trigger_source)
    session = forex_session_state(now)
    news_risk, _ = forex_news_risk()
    created: list[ForexSignalPlan] = []
    reused: list[ForexSignalPlan] = []
    rejected: list[dict[str, object]] = []
    errors: list[str] = []
    telegram_queued = 0
    pairs_evaluated = 0
    quota_exhausted = False
    if not session.market_open:
        finish_scan_run(
            scan_id,
            created_count=0,
            reused_count=0,
            pairs_evaluated=0,
            result_status="MARKET_CLOSED",
            rejection_reasons=["Forex market is closed."],
        )
        return ForexScanRunResult(
            scan_id=scan_id,
            configured=True,
            scanned_at=now,
            completed_at=now,
            timeframe=timeframe,
            trigger_source=trigger_source,
            result_status="MARKET_CLOSED",
            rejection_reasons=["Forex market is closed."],
            created=[],
            reused=[],
            rejected=[
                {
                    "symbol": "FOREX",
                    "decision": "MARKET_CLOSED",
                    "reason": "Forex market is closed.",
                }
            ],
            errors=[],
        )
    try:
        for pair in SUPPORTED_FOREX_PAIRS.values():
            try:
                pairs_evaluated += 1
                candles = await market_data.completed_candles(
                    pair,
                    timeframe,
                    limit=180,
                    now=now,
                )
                if candles.empty:
                    rejected.append(
                        {"symbol": pair.pair, "decision": "DATA_UNAVAILABLE", "reason": "No completed candle data."}
                    )
                    continue
                htf_name = HIGHER_TIMEFRAME[timeframe]
                htf_candles = (
                    await market_data.completed_candles(
                        pair, htf_name, limit=180, now=now
                    )
                    if htf_name else None
                )
                pending_retest = find_waiting_retest(pair.pair, timeframe)
                if pending_retest and now >= pending_retest.expires_at:
                    update_signal_market_state(
                        pending_retest.id,
                        status="EXPIRED",
                        price=float(candles.iloc[-1]["close"]),
                        checked_at=now,
                        closed_at=now,
                    )
                    pending_retest = None
                candle_open_at = pd.Timestamp(candles.iloc[-1]["timestamp"]).isoformat()
                evaluation_key = hashlib.sha256(
                    "|".join(
                        [
                            pair.pair,
                            timeframe,
                            candle_open_at,
                            STRATEGY_FAMILY,
                            STRATEGY_VERSION,
                        ]
                    ).encode("utf-8")
                ).hexdigest()
                previous = get_candle_evaluation(evaluation_key)
                if previous:
                    existing_signal = (
                        get_signal(previous["signal_id"]) if previous.get("signal_id") else None
                    )
                    if existing_signal:
                        reused.append(existing_signal)
                    else:
                        rejected.append(
                            {
                                "symbol": pair.pair,
                                "decision": previous["decision"],
                                "reason": previous.get("reason") or "Candle already evaluated.",
                            }
                        )
                    continue
                plan, audit = analyze_forex_timeframe(
                    pair,
                    candles,
                    htf_candles=htf_candles,
                    pending_retest=pending_retest,
                    timeframe=timeframe,
                    scan_id=scan_id,
                    session_label=session.active_session,
                    news_risk=news_risk,
                    now=now,
                )
                if plan is None:
                    rejected.append(audit)
                    _save_scan_evaluation(
                        evaluation_key=evaluation_key,
                        symbol=pair.pair,
                        timeframe=timeframe,
                        candle_open_at=candle_open_at,
                        audit=audit,
                        signal_id=pending_retest.id if pending_retest else None,
                    )
                    continue
                technical_score = float(plan["setup_score"])
                plan["technical_score"] = technical_score
                final_score = float(plan["setup_score"])
                plan["grade"] = (
                    "WAIT"
                    if plan["status"] == "WAIT_FOR_RETEST"
                    else _grade(final_score)
                )
                if plan["status"] != "WAIT_FOR_RETEST" and final_score < 70:
                    score_audit = {
                        "symbol": pair.pair,
                        "decision": "WAIT_FOR_RETEST" if pending_retest else "NO_TRADE",
                        "reason": (
                            f"RETEST_CONFIRMED_SETUP_SCORE_FAILED: Final setup score {final_score:.1f} "
                            "is below 70 after context."
                            if pending_retest else
                            f"Final setup score {final_score:.1f} is below 70 after context."
                        ),
                    }
                    if pending_retest:
                        score_audit.update({
                            "retest_signal_id": pending_retest.id,
                            "retest_confirmed": True,
                            "retest_level": pending_retest.retest_level,
                            "confirmation_candle_ohlc": audit.get("confirmation_candle_ohlc"),
                            "entry_quality_passed": False,
                            "failing_gate": "RETEST_CONFIRMED_SETUP_SCORE_FAILED",
                            "promotion_occurred": False,
                        })
                    rejected.append(score_audit)
                    _save_scan_evaluation(
                        evaluation_key=evaluation_key,
                        symbol=pair.pair,
                        timeframe=timeframe,
                        candle_open_at=candle_open_at,
                        audit=score_audit,
                        signal_id=pending_retest.id if pending_retest else None,
                    )
                    continue

                existing = find_active_by_dedupe(plan["dedupe_key"])
                if existing and existing.status == "WAIT_FOR_RETEST" and plan["status"] == "PENDING_ENTRY":
                    persisted = promote_retest_signal(
                        existing.id,
                        entry_trigger=plan["entry_trigger"],
                        entry_quality_score=plan["entry_quality_score"],
                        setup_score=plan["setup_score"],
                        grade=plan["grade"],
                        technical_score=plan["technical_score"],
                        setup_reason=plan["setup_reason"],
                        confirmed_at=plan["retest_confirmed_at"],
                    )
                elif existing:
                    reused.append(existing)
                    if existing.status == "WAIT_FOR_RETEST":
                        rejected.append(audit)
                        reuse_audit = audit
                    else:
                        reuse_audit = {
                            "symbol": pair.pair,
                            "decision": "ACTIVE_SIGNAL_REUSED",
                            "reason": "Existing active setup reused; no new actionable trade was created.",
                        }
                    _save_scan_evaluation(
                        evaluation_key=evaluation_key,
                        symbol=pair.pair,
                        timeframe=timeframe,
                        candle_open_at=candle_open_at,
                        audit=reuse_audit,
                        signal_id=existing.id,
                    )
                    continue
                else:
                    persisted = insert_signal(plan)
                created.append(persisted)
                _save_scan_evaluation(
                    evaluation_key=evaluation_key,
                    symbol=pair.pair,
                    timeframe=timeframe,
                    candle_open_at=candle_open_at,
                    audit={
                        **audit,
                        "decision": "TRADE" if persisted.status == "PENDING_ENTRY" else "WAIT_FOR_RETEST",
                    },
                    signal_id=persisted.id,
                    promotion_occurred=bool(
                        pending_retest and persisted.status == "PENDING_ENTRY"
                    ),
                )
                if persisted.status == "PENDING_ENTRY":
                    telegram_queued += enqueue_forex_signal(persisted)
            except ForexProviderNotConfigured:
                raise
            except ForexProviderQuotaExceeded as exc:
                errors.append(str(exc))
                quota_exhausted = True
                break
            except ForexProviderError as exc:
                errors.append(f"{pair.pair}: {exc}")
            except Exception as exc:
                logger.exception(
                    "Forex scan failed pair=%s timeframe=%s", pair.pair, timeframe
                )
                errors.append(f"{pair.pair}: {type(exc).__name__}")
        completed_at = datetime.now(UTC).replace(microsecond=0)
        rejection_counts = Counter(str(item.get("reason") or "Unknown rejection.") for item in rejected)
        rejection_reasons = [
            f"{reason} ({count})" for reason, count in rejection_counts.most_common(4)
        ]
        has_wait = any(item.get("decision") == "WAIT_FOR_RETEST" for item in rejected) or any(
            signal.status == "WAIT_FOR_RETEST" for signal in created + reused
        )
        has_new_trade = any(signal.status == "PENDING_ENTRY" for signal in created)
        has_active_reused = any(
            signal.status in {"PENDING_ENTRY", "OPEN", "TP1_HIT_TP2_RUNNING"}
            for signal in reused
        )
        only_data_unavailable = bool(rejected) and all(
            item.get("decision") == "DATA_UNAVAILABLE" for item in rejected
        )
        result_status = (
            "TRADE_FOUND"
            if has_new_trade
            else "FAILED"
            if quota_exhausted or (errors and not rejected)
            else "WAIT_FOR_RETEST"
            if has_wait
            else "ACTIVE_SIGNAL_REUSED"
            if has_active_reused
            else "DATA_UNAVAILABLE"
            if only_data_unavailable
            else "NO_TRADE"
        )
        finish_scan_run(
            scan_id,
            created_count=len(created),
            reused_count=len(reused),
            pairs_evaluated=pairs_evaluated,
            rejected_count=len(rejected),
            telegram_queued_count=telegram_queued,
            result_status=result_status,
            rejection_reasons=rejection_reasons,
            error_message="; ".join(errors[:3]) if result_status == "FAILED" else None,
        )
        return ForexScanRunResult(
            scan_id=scan_id,
            configured=True,
            scanned_at=now,
            completed_at=completed_at,
            timeframe=timeframe,
            trigger_source=trigger_source,
            result_status=result_status,
            pairs_scanned=pairs_evaluated,
            candidates_found=len(created) + len(reused),
            persisted_count=len(created),
            telegram_queued=telegram_queued,
            rejection_reasons=rejection_reasons,
            created=created,
            reused=reused,
            rejected=rejected,
            errors=errors,
        )
    except ForexProviderNotConfigured as exc:
        completed_at = datetime.now(UTC).replace(microsecond=0)
        finish_scan_run(
            scan_id,
            created_count=0,
            reused_count=0,
            pairs_evaluated=0,
            result_status="FAILED",
            error_message=str(exc),
        )
        return ForexScanRunResult(
            scan_id=scan_id,
            configured=False,
            scanned_at=now,
            completed_at=completed_at,
            timeframe=timeframe,
            trigger_source=trigger_source,
            result_status="FAILED",
            pairs_scanned=0,
            candidates_found=0,
            persisted_count=0,
            telegram_queued=0,
            rejection_reasons=[],
            created=[],
            reused=[],
            rejected=[],
            errors=[str(exc)],
        )


def forex_pair_infos() -> list[ForexPairInfo]:
    defaults = {"execution": "1h", "setup": "4h", "bias": "1d"}
    return [
        ForexPairInfo(
            pair=pair.pair,
            pip_size=pair.pip_size,
            sessions=list(pair.relevant_sessions),
            max_spread_pips=pair.max_spread_pips,
            volatility_rules={
                "min_atr_pips_1h": pair.min_atr_pips_1h,
                "max_atr_pips_1h": pair.max_atr_pips_1h,
            },
            default_timeframes=defaults,
        )
        for pair in SUPPORTED_FOREX_PAIRS.values()
    ]
