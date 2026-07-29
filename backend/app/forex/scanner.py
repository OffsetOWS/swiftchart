from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
import logging
from uuid import uuid4

import pandas as pd

from app.forex.config import (
    DEFAULT_FOREX_TIMEFRAMES,
    STRATEGY_FAMILY,
    STRATEGY_VERSION,
    SUPPORTED_FOREX_PAIRS,
    ForexPairConfig,
)
from app.forex.models import ForexPairInfo, ForexScanRunResult, ForexSignalPlan
from app.forex.news import forex_news_risk
from app.forex.providers import (
    ForexDataProvider,
    ForexProviderError,
    ForexProviderNotConfigured,
    get_forex_provider,
)
from app.forex.sessions import forex_session_state
from app.forex.storage import (
    find_active_by_dedupe,
    finish_scan_run,
    insert_signal,
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
    identifier = f"{candle_time}:{recent_high:.5f}:{recent_low:.5f}:{structure}"
    return structure, identifier


def _entry_confirmation(df: pd.DataFrame, direction: str) -> tuple[bool, str]:
    if len(df) < 12:
        return False, "15M history is insufficient."
    recent = df.tail(12)
    last = recent.iloc[-1]
    prior = recent.iloc[:-1]
    close, open_ = float(last["close"]), float(last["open"])
    high, low = float(last["high"]), float(last["low"])
    prior_high, prior_low = float(prior["high"].max()), float(prior["low"].min())
    if direction == "LONG":
        if close > prior_high:
            return True, "15M bullish breakout closed above the execution range."
        if low <= prior_low and close > open_:
            return True, "15M downside liquidity sweep closed with bullish rejection."
        if close > open_ and close > float(recent["close"].tail(5).mean()):
            return True, "15M bullish continuation candle reclaimed momentum."
    else:
        if close < prior_low:
            return True, "15M bearish breakdown closed below the execution range."
        if high >= prior_high and close < open_:
            return True, "15M upside liquidity sweep closed with bearish rejection."
        if close < open_ and close < float(recent["close"].tail(5).mean()):
            return True, "15M bearish continuation candle rejected momentum."
    return False, "15M execution trigger has not confirmed."


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


async def fetch_timeframe_context(
    provider: ForexDataProvider,
    pair: ForexPairConfig,
) -> dict[str, pd.DataFrame]:
    """Fetch the only three timeframes used by the Forex decision engine."""
    return {
        "15m": await provider.candles(pair, "15m", 180),
        "1h": await provider.candles(pair, "1h", 180),
        "4h": await provider.candles(pair, "4h", 180),
    }


def analyze_forex_pair(
    pair: ForexPairConfig,
    candles: dict[str, pd.DataFrame],
    *,
    scan_id: str,
    session_label: str,
    news_risk: str,
    now: datetime,
) -> tuple[dict | None, dict[str, str | float]]:
    df15, df1h, df4h = candles["15m"], candles["1h"], candles["4h"]
    if min(len(df15), len(df1h), len(df4h)) < 60:
        return None, {"symbol": pair.pair, "reason": "Insufficient 4H/1H/15M candle history."}
    if news_risk == "HIGH":
        return None, {"symbol": pair.pair, "reason": "High-impact news risk."}

    htf_bias = _trend(df4h)
    setup_structure, structure_id = _structure(df1h)
    if htf_bias not in {"bullish", "bearish"} or setup_structure != htf_bias:
        return None, {
            "symbol": pair.pair,
            "reason": f"Alignment failed: 4H {htf_bias}, 1H {setup_structure}.",
        }
    direction = "LONG" if htf_bias == "bullish" else "SHORT"
    entry_ok, entry_trigger = _entry_confirmation(df15, direction)
    if not entry_ok:
        return None, {"symbol": pair.pair, "reason": entry_trigger}

    current = float(df15["close"].iloc[-1])
    execution_atr = max(_atr(df15), pair.pip_size * 4)
    setup_atr = max(_atr(df1h), pair.pip_size * 8)
    half_zone = max(execution_atr * 0.12, pair.pip_size)
    entry_low, entry_high = current - half_zone, current + half_zone
    stop_distance = setup_atr * 0.75
    if direction == "LONG":
        stop = current - stop_distance
        tp1 = current + stop_distance * 1.5
        tp2 = current + stop_distance * 2.4
    else:
        stop = current + stop_distance
        tp1 = current - stop_distance * 1.5
        tp2 = current - stop_distance * 2.4

    volatility_pips = setup_atr / pair.pip_size
    volatility_ok = pair.min_atr_pips_1h <= volatility_pips <= pair.max_atr_pips_1h
    session_ok = session_label in pair.relevant_sessions
    score = round(
        45
        + 20
        + 15
        + (8 if session_ok else 3)
        + (7 if volatility_ok else 2)
        + (5 if news_risk == "LOW" else 2),
        1,
    )
    if score < 70:
        return None, {"symbol": pair.pair, "reason": "Setup score below 70.", "score": score}

    alignment = f"4H {htf_bias} bias + 1H {setup_structure} structure + confirmed 15M trigger"
    raw_key = "|".join(
        [pair.pair, direction, STRATEGY_VERSION, "1h", structure_id, session_label]
    )
    dedupe_key = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
    precision = 3 if pair.pip_size >= 0.01 else 5
    plan = {
        "id": str(uuid4()),
        "symbol": pair.pair,
        "direction": direction,
        "entry_type": "ZONE",
        "entry_price": round(current, precision),
        "entry_low": round(entry_low, precision),
        "entry_high": round(entry_high, precision),
        "stop_loss": round(stop, precision),
        "take_profit_1": round(tp1, precision),
        "take_profit_2": round(tp2, precision),
        "risk_reward_1": _rr(current, stop, tp1),
        "risk_reward_2": _rr(current, stop, tp2),
        "execution_timeframe": DEFAULT_FOREX_TIMEFRAMES["execution"],
        "setup_timeframe": DEFAULT_FOREX_TIMEFRAMES["setup"],
        "bias_timeframe": DEFAULT_FOREX_TIMEFRAMES["bias"],
        "timeframe_alignment": alignment,
        "htf_bias": htf_bias.upper(),
        "setup_structure": f"{setup_structure.upper()}:{structure_id}",
        "entry_trigger": entry_trigger,
        "market_session": session_label,
        "setup_score": score,
        "strategy_family": STRATEGY_FAMILY,
        "strategy_version": STRATEGY_VERSION,
        "market_regime": f"{htf_bias.title()} trend",
        "bias": htf_bias.upper(),
        "setup_reason": (
            f"{pair.pair} aligned across 4H bias and 1H structure. {entry_trigger} "
            f"Session={session_label}; 1H ATR={volatility_pips:.1f} pips."
        ),
        "status": "PENDING_ENTRY",
        "created_at": now,
        "expires_at": now + timedelta(hours=12),
        "source_scan_id": scan_id,
        "dedupe_key": dedupe_key,
        "grade": _grade(score),
        "news_risk": news_risk,
        "spread_status": "SAFE",
    }
    return plan, {"symbol": pair.pair, "reason": "qualified", "score": score}


async def scan_forex(provider: ForexDataProvider | None = None) -> ForexScanRunResult:
    now = datetime.now(UTC).replace(microsecond=0)
    scan_id = str(uuid4())
    provider = provider or get_forex_provider()
    start_scan_run(scan_id, provider.name, now)
    session = forex_session_state(now)
    news_risk, _ = forex_news_risk()
    created: list[ForexSignalPlan] = []
    reused: list[ForexSignalPlan] = []
    rejected: list[dict[str, str | float]] = []
    errors: list[str] = []
    try:
        for pair in SUPPORTED_FOREX_PAIRS.values():
            try:
                context = await fetch_timeframe_context(provider, pair)
                plan, audit = analyze_forex_pair(
                    pair,
                    context,
                    scan_id=scan_id,
                    session_label=session.active_session,
                    news_risk=news_risk,
                    now=now,
                )
                if plan is None:
                    rejected.append(audit)
                    continue
                existing = find_active_by_dedupe(plan["dedupe_key"])
                if existing:
                    reused.append(existing)
                    continue
                persisted = insert_signal(plan)
                created.append(persisted)
                enqueue_forex_signal(persisted)
            except ForexProviderNotConfigured:
                raise
            except ForexProviderError as exc:
                errors.append(f"{pair.pair}: {exc}")
            except Exception as exc:
                logger.exception("Forex scan failed pair=%s", pair.pair)
                errors.append(f"{pair.pair}: {type(exc).__name__}")
        finish_scan_run(scan_id, created_count=len(created), reused_count=len(reused))
        return ForexScanRunResult(
            scan_id=scan_id,
            configured=True,
            scanned_at=now,
            created=created,
            reused=reused,
            rejected=rejected,
            errors=errors,
        )
    except ForexProviderNotConfigured as exc:
        finish_scan_run(scan_id, created_count=0, reused_count=0, error_message=str(exc))
        return ForexScanRunResult(
            scan_id=scan_id,
            configured=False,
            scanned_at=now,
            created=[],
            reused=[],
            rejected=[],
            errors=[str(exc)],
        )


def forex_pair_infos() -> list[ForexPairInfo]:
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
            default_timeframes=DEFAULT_FOREX_TIMEFRAMES,
        )
        for pair in SUPPORTED_FOREX_PAIRS.values()
    ]
