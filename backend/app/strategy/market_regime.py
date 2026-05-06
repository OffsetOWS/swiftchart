from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd

from app.models.schemas import MarketRegimeSnapshot
from app.strategy.market_structure import higher_timeframe_bias, trend_direction


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


def _bias(label: str) -> tuple[str, str, str]:
    if "Bullish" in label:
        return "Long bias", "Prioritize longs", "Counter-trend shorts require strong reversal confirmation"
    if "Bearish" in label:
        return "Short bias", "Counter-trend longs require strong reversal confirmation", "Prioritize shorts"
    return "Balanced", "Range/mean-reversion longs allowed", "Range/mean-reversion shorts allowed"


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
    label = _label(score)
    bias, long_bias, short_bias = _bias(label)
    close = df["close"].astype(float)
    ema50 = _ema(close, 50)
    ema200 = _ema(close, 200) if len(close) >= 200 else _ema(close, min(100, len(close)))
    rsi = round(_rsi(close), 1)
    adx = _adx(df)
    htf = higher_timeframe_bias(htf_dfs)
    components = {
        "local_score": local_score,
        "global_score": global_score,
        "breadth_above_ma_pct": breadth_above_ma_pct,
        "higher_timeframe_bias": htf,
        "ema_50": round(float(ema50.iloc[-1]), 6),
        "ema_200": round(float(ema200.iloc[-1]), 6),
        "ema_slope": round((float(ema50.iloc[-1]) - float(ema50.iloc[max(0, len(ema50) - 13)])) / max(abs(float(ema50.iloc[-1])), 1e-9), 5),
        "rsi": rsi,
        "adx": adx,
    }
    explanation = f"{label} ({score:+.0f}) from HTF={htf}, EMA structure, EMA slope, RSI={rsi}, ADX={adx or '-'}."
    if global_score is not None:
        explanation += f" BTC/ETH global bias contributes {global_score:+.0f}."
    if breadth_above_ma_pct is not None:
        explanation += f" Breadth above key MAs is {breadth_above_ma_pct:.0f}%."
    return MarketRegimeSnapshot(
        score=score,
        label=label,
        bias=bias,
        long_bias=long_bias,
        short_bias=short_bias,
        updated_at=datetime.now(UTC).replace(microsecond=0),
        components=components,
        explanation=explanation,
    )
