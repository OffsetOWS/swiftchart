import pandas as pd

from app.models.schemas import MarketCondition, Zone
from app.strategy.support_resistance import detect_swings


def volume_confirmation(df: pd.DataFrame, period: int = 20) -> bool:
    if len(df) < period + 1:
        return False
    return float(df["volume"].iloc[-1]) > float(df["volume"].tail(period).mean()) * 1.2


def momentum_confirmation(df: pd.DataFrame, period: int = 14) -> bool:
    if len(df) < period + 1:
        return False
    recent_return = df["close"].pct_change(period).iloc[-1]
    return abs(float(recent_return)) > 0.025


def classify_market(df: pd.DataFrame, support: Zone | None, resistance: Zone | None) -> MarketCondition:
    price = float(df["close"].iloc[-1])
    vol_ok = volume_confirmation(df)
    mom_ok = momentum_confirmation(df)

    if resistance and price > resistance.upper and (vol_ok or mom_ok):
        return "Breakout"
    if support and price < support.lower and (vol_ok or mom_ok):
        return "Breakdown"

    if support and resistance and support.upper < price < resistance.lower:
        range_size = resistance.lower - support.upper
        if range_size > 0:
            position = (price - support.upper) / range_size
            if 0.38 <= position <= 0.62:
                return "No-trade zone"
            return "Range-bound"

    swings = detect_swings(df.tail(90).reset_index(drop=True))
    highs = swings.loc[swings["swing_high"], "high"].tail(3).tolist()
    lows = swings.loc[swings["swing_low"], "low"].tail(3).tolist()
    if len(highs) >= 2 and len(lows) >= 2:
        if highs[-1] > highs[-2] and lows[-1] > lows[-2]:
            return "Trending up"
        if highs[-1] < highs[-2] and lows[-1] < lows[-2]:
            return "Trending down"

    return "Range-bound"
