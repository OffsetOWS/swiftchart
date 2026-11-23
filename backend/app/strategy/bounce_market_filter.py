"""Pre-registered broad-market gates for the counter-trend bounce setup."""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd

from app.models.schemas import MarketRegimeSnapshot
from app.strategy.market_regime import detect_market_regime, regime_score_from_dataframe
from app.strategy.strict_trend_short import candle_is_completed

FILTER_MODES = {"F1", "F2", "F3"}


def completed_candles(df: pd.DataFrame, timeframe: str, *, as_of: datetime | None = None) -> pd.DataFrame:
    """Return only candles known to be complete at the supplied scan time."""
    if df.empty or "timestamp" not in df:
        return df.iloc[0:0].copy()
    now = as_of or datetime.now(UTC)
    if "complete" in df.columns:
        mask = df["complete"].fillna(False).astype(bool)
    else:
        mask = df["timestamp"].map(lambda value: candle_is_completed(value, timeframe, as_of=now))
    return df.loc[mask].copy().reset_index(drop=True)


def build_btc_market_context(
    btc_4h: pd.DataFrame,
    btc_1d: pd.DataFrame,
    eth_4h: pd.DataFrame | None = None,
) -> dict:
    """Build the three fixed filter inputs from completed candles only."""
    regime: str | None = None
    if len(btc_4h) >= 80:
        regime = detect_market_regime(btc_4h, []).regime_type

    if eth_4h is not None and len(btc_4h) >= 80 and len(eth_4h) >= 80:
        global_score_4h = round(
            (regime_score_from_dataframe(btc_4h) + regime_score_from_dataframe(eth_4h)) / 2,
            1,
        )
    else:
        global_score_4h = None

    below_ema50 = None
    ema50_slope_10d = None
    if len(btc_1d) >= 60:
        close = pd.to_numeric(btc_1d["close"], errors="coerce")
        ema = close.ewm(span=50, adjust=False).mean()
        below_ema50 = bool(close.iloc[-1] < ema.iloc[-1])
        ema50_slope_10d = float(ema.iloc[-1] - ema.iloc[-11])

    return {
        "btc_4h_regime": regime,
        "global_regime_score_4h": global_score_4h,
        "btc_1d_below_ema50": below_ema50,
        "btc_1d_ema50_slope_10d": ema50_slope_10d,
    }


def blocks_bounce_long(mode: str, context: dict | None) -> bool:
    """Apply the preregistered F1/F2/F3 thresholds; missing context fails closed."""
    selected = str(mode or "F1").upper()
    if selected not in FILTER_MODES:
        return True
    if not context:
        return True
    if selected == "F1":
        regime = context.get("btc_4h_regime")
        if regime is None:
            return True
        return regime in {"TRENDING_DOWN", "BREAKDOWN"}
    if selected == "F2":
        score = context.get("global_regime_score_4h")
        return score is None or float(score) <= -45
    below = context.get("btc_1d_below_ema50")
    slope = context.get("btc_1d_ema50_slope_10d")
    return below is None or slope is None or (bool(below) and float(slope) < 0)
