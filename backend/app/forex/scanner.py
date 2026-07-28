from __future__ import annotations

from datetime import UTC, datetime
import logging

import pandas as pd

from app.forex.config import DEFAULT_FOREX_TIMEFRAMES, SUPPORTED_FOREX_PAIRS, ForexPairConfig
from app.forex.models import ForexScanResponse, ForexSignal
from app.forex.news import forex_news_risk
from app.forex.providers import ForexDataProvider, ForexProviderError, ForexProviderNotConfigured, get_forex_provider
from app.forex.sessions import forex_session_state
from app.forex.storage import save_forex_signals

logger = logging.getLogger(__name__)


def _ema(series: pd.Series, span: int) -> float:
    if series.empty:
        return 0.0
    return float(series.ewm(span=min(span, len(series)), adjust=False).mean().iloc[-1])


def _trend(df: pd.DataFrame) -> str:
    if len(df) < 20:
        return "neutral"
    close = df["close"].astype(float)
    price = float(close.iloc[-1])
    fast = _ema(close, 20)
    slow = _ema(close, 50)
    slope = fast - _ema(close.iloc[:-5], 20) if len(close) > 25 else 0
    if price > fast > slow and slope >= 0:
        return "bullish"
    if price < fast < slow and slope <= 0:
        return "bearish"
    return "neutral"


def _atr(df: pd.DataFrame) -> float:
    if len(df) < 2:
        return 0.0
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = df["close"].astype(float)
    true_range = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
    return float(true_range.tail(14).mean())


def _rr(entry: float, stop: float, target: float, direction: str) -> float:
    risk = abs(entry - stop)
    if risk <= 0:
        return 0.0
    reward = target - entry if direction == "LONG" else entry - target
    return round(max(0.0, reward / risk), 2)


def _grade(score: float) -> str:
    if score >= 90:
        return "A+"
    if score >= 80:
        return "A"
    if score >= 70:
        return "B"
    if score >= 60:
        return "C"
    return "WAIT"


def _structure_direction(df: pd.DataFrame) -> str:
    if len(df) < 30:
        return "neutral"
    recent = df.tail(30)
    previous = df.iloc[-60:-30] if len(df) >= 60 else df.head(30)
    if previous.empty:
        return "neutral"
    higher_high = float(recent["high"].max()) > float(previous["high"].max())
    higher_low = float(recent["low"].min()) > float(previous["low"].min())
    lower_high = float(recent["high"].max()) < float(previous["high"].max())
    lower_low = float(recent["low"].min()) < float(previous["low"].min())
    if higher_high and higher_low:
        return "bullish"
    if lower_high and lower_low:
        return "bearish"
    return "neutral"


def _entry_confirmation(df: pd.DataFrame, direction: str) -> tuple[bool, str]:
    if len(df) < 12:
        return False, "Entry timeframe has insufficient candle structure."
    recent = df.tail(12)
    last = recent.iloc[-1]
    recent_high = float(recent["high"].iloc[:-1].max())
    recent_low = float(recent["low"].iloc[:-1].min())
    close = float(last["close"])
    high = float(last["high"])
    low = float(last["low"])
    open_ = float(last["open"])
    if direction == "LONG":
        if close > recent_high:
            return True, "15m bullish breakout confirmation."
        if low <= recent_low and close > open_:
            return True, "15m liquidity sweep and bullish rejection."
        if close > open_ and close > float(recent["close"].tail(5).mean()):
            return True, "15m continuation candle confirms bullish timing."
    if direction == "SHORT":
        if close < recent_low:
            return True, "15m bearish breakdown confirmation."
        if high >= recent_high and close < open_:
            return True, "15m liquidity sweep and bearish rejection."
        if close < open_ and close < float(recent["close"].tail(5).mean()):
            return True, "15m continuation candle confirms bearish timing."
    return False, "No clean 15m breakout, rejection, continuation, or sweep confirmation."


def _location_quality(df: pd.DataFrame, direction: str) -> tuple[float, str]:
    if len(df) < 40:
        return 0.0, "Location quality limited by candle history."
    recent = df.tail(60)
    price = float(recent["close"].iloc[-1])
    support = float(recent["low"].min())
    resistance = float(recent["high"].max())
    width = max(resistance - support, 1e-9)
    position = (price - support) / width
    if direction == "LONG":
        quality = max(0.0, 1 - position)
        return quality, "Near support/liquidity discount." if quality >= 0.5 else "Long location is not close to support."
    quality = position
    return quality, "Near resistance/liquidity premium." if quality >= 0.5 else "Short location is not close to resistance."


def _spread_status(pair: ForexPairConfig) -> tuple[str, float]:
    configured_spread = pair.max_spread_pips * 0.55
    if configured_spread <= pair.max_spread_pips:
        return "SAFE", 5.0
    return "WIDE", 0.0


def _volatility_points(pair: ForexPairConfig, atr_value: float) -> tuple[float, str]:
    atr_pips = atr_value / pair.pip_size if pair.pip_size else 0
    if pair.min_atr_pips_1h <= atr_pips <= pair.max_atr_pips_1h:
        return 5.0, f"Volatility is tradable at {atr_pips:.1f} pips ATR."
    if atr_pips < pair.min_atr_pips_1h:
        return 1.0, f"Volatility is muted at {atr_pips:.1f} pips ATR."
    return 2.0, f"Volatility is elevated at {atr_pips:.1f} pips ATR."


def _session_points(pair: ForexPairConfig, session: str, pre_session: bool) -> tuple[float, str]:
    if pre_session:
        return 8.0, "Pre-session bias only; no need to force an entry before open."
    if session in pair.relevant_sessions:
        return 15.0, f"{session} is a relevant session for {pair.pair}."
    if session == "Closed":
        return 0.0, "Forex market is currently closed."
    return 7.0, f"{session} is usable but not the primary session for {pair.pair}."


async def _fetch_context(provider: ForexDataProvider, pair: ForexPairConfig) -> dict[str, pd.DataFrame]:
    return {
        "15m": await provider.candles(pair, "15m", 180),
        "1h": await provider.candles(pair, "1h", 180),
        "4h": await provider.candles(pair, "4h", 180),
        "1d": await provider.candles(pair, "1d", 180),
    }


def _wait_signal(pair: ForexPairConfig, session: str, news_risk: str, reason: str, now: datetime) -> ForexSignal:
    return ForexSignal(
        pair=pair.pair,
        direction="WAIT",
        score=0,
        grade="WAIT",
        session=session,
        pre_session_bias="Neutral / wait",
        spreadStatus="UNKNOWN",
        newsRisk=news_risk,  # type: ignore[arg-type]
        reason=reason,
        lastUpdated=now,
        status="wait",
    )


def analyze_forex_pair(pair: ForexPairConfig, candles: dict[str, pd.DataFrame], *, session_label: str, is_pre_session: bool, news_risk: str, now: datetime) -> ForexSignal:
    df15 = candles["15m"]
    df1h = candles["1h"]
    df4h = candles["4h"]
    df1d = candles["1d"]
    if min(len(df15), len(df1h), len(df4h), len(df1d)) < 30:
        return _wait_signal(pair, session_label, news_risk, "Not enough forex candle history.", now)
    if news_risk == "HIGH":
        return _wait_signal(pair, session_label, news_risk, "High-impact news risk", now)

    trend_1d = _trend(df1d)
    trend_4h = _trend(df4h)
    structure_1h = _structure_direction(df1h)
    bullish_votes = [trend_1d, trend_4h, structure_1h].count("bullish")
    bearish_votes = [trend_1d, trend_4h, structure_1h].count("bearish")
    direction = "LONG" if bullish_votes >= 2 else "SHORT" if bearish_votes >= 2 else "WAIT"
    pre_session_bias = f"{trend_4h.title()} 4H + {structure_1h.title()} 1H structure"
    if direction == "WAIT":
        return _wait_signal(pair, session_label, news_risk, f"Mixed forex context: 1D {trend_1d}, 4H {trend_4h}, 1H {structure_1h}.", now).model_copy(
            update={"score": 45, "pre_session_bias": pre_session_bias}
        )

    entry_ok, entry_reason = _entry_confirmation(df15, direction)
    location, location_reason = _location_quality(df1h, direction)
    spread_status, spread_points = _spread_status(pair)
    volatility_points, volatility_reason = _volatility_points(pair, _atr(df1h))
    session_points, session_reason = _session_points(pair, session_label, is_pre_session)
    news_points = 5.0 if news_risk == "LOW" else 2.0 if news_risk == "MEDIUM" else 0.0
    htf_points = 20.0 if (trend_1d == trend_4h and trend_4h in {"bullish", "bearish"}) else 13.0
    structure_points = 15.0 if structure_1h == ("bullish" if direction == "LONG" else "bearish") else 7.0
    entry_points = 15.0 if entry_ok and not is_pre_session else 8.0 if entry_ok else 0.0
    location_points = min(10.0, max(0.0, location * 10.0))
    price = float(df15["close"].iloc[-1])
    atr = max(_atr(df1h), pair.pip_size * 8)
    stop_distance = atr * 0.75
    if direction == "LONG":
        entry = price
        stop = price - stop_distance
        tp1 = price + stop_distance * 1.6
        tp2 = price + stop_distance * 2.4
    else:
        entry = price
        stop = price + stop_distance
        tp1 = price - stop_distance * 1.6
        tp2 = price - stop_distance * 2.4
    rr = _rr(entry, stop, tp2, direction)
    rr_points = 10.0 if rr >= 2 else 7.0 if rr >= 1.5 else 3.0
    score = round(
        htf_points
        + structure_points
        + session_points
        + entry_points
        + location_points
        + rr_points
        + spread_points
        + news_points
        + volatility_points,
        1,
    )
    if score < 70 or is_pre_session:
        wait_reason = "Pre-session bias prepared; waiting for session confirmation." if is_pre_session else "Forex setup score is below 70."
        return ForexSignal(
            pair=pair.pair,
            direction="WAIT",
            score=score,
            grade=_grade(score),
            session=session_label,
            pre_session_bias=pre_session_bias,
            entry=round(entry, 5),
            stopLoss=round(stop, 5),
            tp1=round(tp1, 5),
            tp2=round(tp2, 5),
            rr=rr,
            spreadStatus=spread_status,  # type: ignore[arg-type]
            newsRisk=news_risk,  # type: ignore[arg-type]
            reason=f"{wait_reason} {entry_reason} {session_reason}",
            lastUpdated=now,
            status="wait",
        )
    return ForexSignal(
        pair=pair.pair,
        direction=direction,  # type: ignore[arg-type]
        score=score,
        grade=_grade(score),
        session=session_label,
        pre_session_bias=pre_session_bias,
        entry=round(entry, 5),
        stopLoss=round(stop, 5),
        tp1=round(tp1, 5),
        tp2=round(tp2, 5),
        rr=rr,
        spreadStatus=spread_status,  # type: ignore[arg-type]
        newsRisk=news_risk,  # type: ignore[arg-type]
        reason=f"{session_label} session setup. {entry_reason} {location_reason} {volatility_reason}",
        lastUpdated=now,
        status="active",
    )


async def scan_forex(provider: ForexDataProvider | None = None, *, save: bool = True) -> ForexScanResponse:
    now = datetime.now(UTC).replace(microsecond=0)
    session = forex_session_state(now)
    active_session = session.active_session
    provider = provider or get_forex_provider()
    signals: list[ForexSignal] = []
    errors: list[str] = []
    news_risk, news_reason = forex_news_risk()
    try:
        for pair in SUPPORTED_FOREX_PAIRS.values():
            try:
                context = await _fetch_context(provider, pair)
                signals.append(
                    analyze_forex_pair(
                        pair,
                        context,
                        session_label=active_session,
                        is_pre_session=session.is_pre_session,
                        news_risk=news_risk,
                        now=now,
                    )
                )
            except ForexProviderNotConfigured:
                raise
            except ForexProviderError as exc:
                errors.append(f"{pair.pair}: {exc}")
            except Exception as exc:
                logger.exception("Forex scan failed for pair=%s", pair.pair)
                errors.append(f"{pair.pair}: {type(exc).__name__}")
    except ForexProviderNotConfigured:
        return ForexScanResponse(
            configured=False,
            provider=provider.name,
            activeSession=session,
            signals=[],
            topSetups=[],
            supportedPairs=forex_pair_infos(),
            newsRisk=news_risk,
            message="Forex data provider is not configured.",
            scannedAt=now,
            errors=[],
        )
    if news_risk == "HIGH":
        message = "Forex scan paused due to high-impact news risk."
    elif not session.market_open:
        message = "Forex market is currently closed. Next session opens soon."
    elif not signals:
        message = "No clean forex setups right now."
    else:
        message = None
    top = [signal for signal in signals if signal.direction != "WAIT" and signal.score >= 70]
    top = sorted(top, key=lambda signal: signal.score, reverse=True)
    if save:
        save_forex_signals(signals)
    return ForexScanResponse(
        configured=True,
        provider=provider.name,
        activeSession=session,
        signals=signals,
        topSetups=top,
        supportedPairs=forex_pair_infos(),
        newsRisk=news_risk,
        message=message,
        scannedAt=now,
        errors=errors,
    )


def forex_pair_infos():
    from app.forex.models import ForexPairInfo

    return [
        ForexPairInfo(
            pair=pair.pair,
            pipSize=pair.pip_size,
            sessions=list(pair.relevant_sessions),
            maxSpreadPips=pair.max_spread_pips,
            volatilityRules={"minAtrPips1h": pair.min_atr_pips_1h, "maxAtrPips1h": pair.max_atr_pips_1h},
            defaultTimeframes=DEFAULT_FOREX_TIMEFRAMES,
        )
        for pair in SUPPORTED_FOREX_PAIRS.values()
    ]
