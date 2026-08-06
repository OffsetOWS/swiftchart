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
from app.forex.context import evaluate_cross_market_context, unavailable_cross_market_context
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
    finish_scan_run,
    get_candle_evaluation,
    get_signal,
    insert_signal,
    save_candle_evaluation,
    start_scan_run,
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


def _entry_confirmation(
    df: pd.DataFrame,
    direction: str,
    timeframe: str,
) -> tuple[bool, str]:
    if len(df) < 12:
        return False, f"{timeframe} history is insufficient."
    recent = df.tail(12)
    last = recent.iloc[-1]
    prior = recent.iloc[:-1]
    close, open_ = float(last["close"]), float(last["open"])
    high, low = float(last["high"]), float(last["low"])
    prior_high, prior_low = float(prior["high"].max()), float(prior["low"].min())
    if direction == "LONG":
        if close > prior_high:
            return True, f"{timeframe} bullish breakout closed above its range."
        if low <= prior_low and close > open_:
            return True, f"{timeframe} downside liquidity sweep closed with bullish rejection."
        if close > open_ and close > float(recent["close"].tail(5).mean()):
            return True, f"{timeframe} bullish continuation reclaimed momentum."
    else:
        if close < prior_low:
            return True, f"{timeframe} bearish breakdown closed below its range."
        if high >= prior_high and close < open_:
            return True, f"{timeframe} upside liquidity sweep closed with bearish rejection."
        if close < open_ and close < float(recent["close"].tail(5).mean()):
            return True, f"{timeframe} bearish continuation rejected momentum."
    return False, f"{timeframe} trigger has not confirmed."


def _atr(df: pd.DataFrame) -> float:
    high, low, close = (df[column].astype(float) for column in ("high", "low", "close"))
    ranges = pd.concat(
        [high - low, (high - close.shift()).abs(), (low - close.shift()).abs()],
        axis=1,
    ).max(axis=1)
    return float(ranges.tail(14).mean())


def _rr(entry: float, stop: float, target: float) -> float:
    risk = abs(entry - stop)
    return round(abs(target - entry) / risk, 2) if risk else 0.0


def _grade(score: float) -> str:
    return "A+" if score >= 90 else "A" if score >= 80 else "B"


def analyze_forex_timeframe(
    pair: ForexPairConfig,
    candles: pd.DataFrame,
    *,
    timeframe: str,
    scan_id: str,
    session_label: str,
    news_risk: str,
    now: datetime,
) -> tuple[dict | None, dict[str, str | float]]:
    timeframe = normalize_forex_timeframe(timeframe)
    if len(candles) < 60:
        return None, {"symbol": pair.pair, "decision": "NO_TRADE", "reason": f"Insufficient {timeframe} candle history."}
    if news_risk == "HIGH":
        return None, {"symbol": pair.pair, "decision": "NO_TRADE", "reason": "High-impact news risk."}

    regime = _trend(candles)
    structure, structure_id = _structure(candles)
    if regime not in {"bullish", "bearish"} or structure != regime:
        return None, {
            "symbol": pair.pair,
            "decision": "NO_TRADE",
            "reason": f"{timeframe} regime={regime}, structure={structure}.",
        }

    direction = "LONG" if regime == "bullish" else "SHORT"
    entry_ok, entry_trigger = _entry_confirmation(candles, direction, timeframe)
    if not entry_ok:
        return None, {"symbol": pair.pair, "decision": "WAIT_FOR_RETEST", "reason": entry_trigger}

    current = float(candles["close"].iloc[-1])
    timeframe_atr = max(_atr(candles), pair.pip_size * 4)
    candle_range = float(candles["high"].iloc[-1]) - float(candles["low"].iloc[-1])
    distance_from_mean = abs(current - _ema(candles["close"].astype(float), 20))
    if candle_range > timeframe_atr * 1.8 or distance_from_mean > timeframe_atr * 1.6:
        return None, {
            "symbol": pair.pair,
            "decision": "WAIT_FOR_RETEST",
            "reason": f"{timeframe} entry is extended; waiting for a non-exhausted retest.",
        }
    half_zone = max(timeframe_atr * 0.12, pair.pip_size)
    entry_low, entry_high = current - half_zone, current + half_zone
    recent_structure = candles.tail(12)
    if direction == "LONG":
        stop = float(recent_structure["low"].min()) - timeframe_atr * 0.08
        stop_distance = current - stop
        tp1 = current + stop_distance * 1.5
        tp2 = current + stop_distance * 2.4
    else:
        stop = float(recent_structure["high"].max()) + timeframe_atr * 0.08
        stop_distance = stop - current
        tp1 = current - stop_distance * 1.5
        tp2 = current - stop_distance * 2.4

    settings = get_settings()
    stop_pips = stop_distance / pair.pip_size
    timeframe_stop_multiplier = {"15M": 1.0, "1H": 1.0, "4H": 2.5, "1D": 6.0}[timeframe]
    maximum_stop = min(
        settings.forex_max_stop_pips,
        pair.max_atr_pips_1h * timeframe_stop_multiplier,
    )
    if stop_pips < settings.forex_min_stop_pips or stop_pips > maximum_stop:
        return None, {
            "symbol": pair.pair,
            "decision": "NO_TRADE",
            "reason": f"Structural stop distance {stop_pips:.1f} pips is outside risk limits.",
        }

    session_ok = session_label in pair.relevant_sessions
    score = round(45 + 20 + 15 + (8 if session_ok else 3) + (5 if news_risk == "LOW" else 2), 1)
    if score < 70:
        return None, {"symbol": pair.pair, "decision": "NO_TRADE", "reason": "Setup score below 70.", "score": score}

    strategy_family = f"{STRATEGY_FAMILY}_{timeframe.lower()}"
    # Active consecutive candles in the same directional structure are one setup,
    # while per-candle evaluation is tracked separately.
    raw_key = "|".join(
        [pair.pair, timeframe, direction, strategy_family, STRATEGY_VERSION, structure]
    )
    dedupe_key = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
    precision = 3 if pair.pip_size >= 0.01 else 5
    plan = {
        "id": str(uuid4()),
        "symbol": pair.pair,
        "timeframe": timeframe,
        "direction": direction,
        "entry_type": "ZONE",
        "entry_price": round(current, precision),
        "entry_low": round(entry_low, precision),
        "entry_high": round(entry_high, precision),
        "stop_loss": round(stop, precision),
        "take_profit_1": round(tp1, precision),
        "take_profit_2": round(tp2, precision),
        "tp1_closes_position": get_settings().forex_close_entire_position_at_tp1,
        "risk_reward_1": _rr(current, stop, tp1),
        "risk_reward_2": _rr(current, stop, tp2),
        # Compatibility fields remain populated, but no cross-timeframe input is used.
        "execution_timeframe": timeframe.lower(),
        "setup_timeframe": timeframe.lower(),
        "bias_timeframe": timeframe.lower(),
        "timeframe_alignment": f"{timeframe} independent structure strategy",
        "htf_bias": regime.upper(),
        "setup_structure": f"{structure.upper()}:{structure_id}",
        "entry_trigger": entry_trigger,
        "market_session": session_label,
        "setup_score": score,
        "strategy_family": strategy_family,
        "strategy_version": STRATEGY_VERSION,
        "market_regime": f"{regime.title()} {timeframe} trend",
        "bias": regime.upper(),
        "setup_reason": (
            f"{pair.pair} qualified independently on {timeframe}. {entry_trigger} "
            f"{timeframe} structure is {structure}; session={session_label}."
        ),
        "status": "PENDING_ENTRY",
        "created_at": now,
        "expires_at": now + timedelta(hours=TIMEFRAME_EXPIRY_HOURS[timeframe]),
        "source_scan_id": scan_id,
        "dedupe_key": dedupe_key,
        "grade": _grade(score),
        "news_risk": news_risk,
        "spread_status": "SAFE",
    }
    return plan, {"symbol": pair.pair, "decision": "TRADE", "reason": "qualified", "score": score}


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
    rejected: list[dict[str, str | float]] = []
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
                    timeframe=timeframe,
                    scan_id=scan_id,
                    session_label=session.active_session,
                    news_risk=news_risk,
                    now=now,
                )
                if plan is None:
                    rejected.append(audit)
                    save_candle_evaluation(
                        evaluation_key=evaluation_key,
                        symbol=pair.pair,
                        timeframe=timeframe,
                        candle_open_at=candle_open_at,
                        strategy_family=STRATEGY_FAMILY,
                        strategy_version=STRATEGY_VERSION,
                        decision=str(audit.get("decision") or "NO_TRADE"),
                        reason=str(audit.get("reason") or "No valid setup."),
                    )
                    continue
                existing = find_active_by_dedupe(plan["dedupe_key"])
                if existing:
                    reused.append(existing)
                    save_candle_evaluation(
                        evaluation_key=evaluation_key,
                        symbol=pair.pair,
                        timeframe=timeframe,
                        candle_open_at=candle_open_at,
                        strategy_family=STRATEGY_FAMILY,
                        strategy_version=STRATEGY_VERSION,
                        decision="TRADE",
                        reason="Existing active setup reused.",
                        signal_id=existing.id,
                    )
                    continue
                context = (
                    await evaluate_cross_market_context(
                        pair.pair,
                        plan["direction"],
                        timeframe,
                        market_data,
                        now=now,
                    )
                    if getattr(market_data.provider, "supports_cross_market_context", False)
                    else unavailable_cross_market_context(pair.pair, timeframe, now)
                )
                technical_score = float(plan["setup_score"])
                plan["technical_score"] = technical_score
                plan["context_adjustment"] = context.total_adjustment
                plan["cross_market_context"] = context
                plan["setup_score"] = max(
                    0.0,
                    min(100.0, round(technical_score + context.total_adjustment, 1)),
                )
                plan["setup_reason"] = f"{plan['setup_reason']} {context.explanation}"
                persisted = insert_signal(plan)
                created.append(persisted)
                save_candle_evaluation(
                    evaluation_key=evaluation_key,
                    symbol=pair.pair,
                    timeframe=timeframe,
                    candle_open_at=candle_open_at,
                    strategy_family=STRATEGY_FAMILY,
                    strategy_version=STRATEGY_VERSION,
                    decision="TRADE",
                    reason=str(audit.get("reason") or "Qualified."),
                    signal_id=persisted.id,
                )
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
        has_wait = any(item.get("decision") == "WAIT_FOR_RETEST" for item in rejected)
        only_data_unavailable = bool(rejected) and all(
            item.get("decision") == "DATA_UNAVAILABLE" for item in rejected
        )
        result_status = (
            "TRADE_FOUND"
            if created or reused
            else "FAILED"
            if quota_exhausted or (errors and not rejected)
            else "WAIT_FOR_RETEST"
            if has_wait
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
