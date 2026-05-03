import pandas as pd

from app.models.schemas import MarketCondition, Zone
from app.strategy.support_resistance import average_true_range, detect_swings


def volume_confirmation(df: pd.DataFrame, period: int = 20) -> bool:
    if len(df) < period + 1:
        return False
    return float(df["volume"].iloc[-1]) > float(df["volume"].tail(period).mean()) * 1.25


def momentum_confirmation(df: pd.DataFrame, period: int = 14) -> bool:
    if len(df) < period + 1:
        return False
    recent_return = df["close"].pct_change(period).iloc[-1]
    atr = average_true_range(df)
    return abs(float(recent_return)) > 0.025 or abs(float(df["close"].iloc[-1] - df["close"].iloc[-period])) > atr * 2.2


def range_position(price: float, support: Zone | None, resistance: Zone | None) -> float | None:
    if support is None or resistance is None:
        return None
    width = resistance.lower - support.upper
    if width <= 0:
        return None
    return max(0.0, min(1.0, (price - support.upper) / width))


def trend_direction(df: pd.DataFrame, lookback: int = 100) -> str:
    if len(df) < 40:
        return "neutral"
    recent = df.tail(lookback).reset_index(drop=True)
    fast = recent["close"].ewm(span=21, adjust=False).mean()
    slow = recent["close"].ewm(span=55, adjust=False).mean()
    slope = float(slow.iloc[-1] - slow.iloc[max(0, len(slow) - 12)]) / max(abs(float(slow.iloc[-1])), 1e-9)

    swings = detect_swings(recent)
    highs = swings.loc[swings["swing_high"], "high"].tail(3).tolist()
    lows = swings.loc[swings["swing_low"], "low"].tail(3).tolist()
    higher_highs = len(highs) >= 2 and highs[-1] > highs[-2]
    higher_lows = len(lows) >= 2 and lows[-1] > lows[-2]
    lower_highs = len(highs) >= 2 and highs[-1] < highs[-2]
    lower_lows = len(lows) >= 2 and lows[-1] < lows[-2]

    if fast.iloc[-1] > slow.iloc[-1] and slope > 0.002 and (higher_highs or higher_lows):
        return "up"
    if fast.iloc[-1] < slow.iloc[-1] and slope < -0.002 and (lower_highs or lower_lows):
        return "down"
    return "neutral"


def classify_market(df: pd.DataFrame, support: Zone | None, resistance: Zone | None) -> MarketCondition:
    price = float(df["close"].iloc[-1])
    atr = average_true_range(df)
    vol_ok = volume_confirmation(df)
    mom_ok = momentum_confirmation(df)

    if resistance and price > resistance.upper + atr * 0.15:
        return "BREAKOUT" if vol_ok or mom_ok else "NO_TRADE"
    if support and price < support.lower - atr * 0.15:
        return "BREAKDOWN" if vol_ok or mom_ok else "NO_TRADE"

    position = range_position(price, support, resistance)
    if position is not None:
        width = resistance.lower - support.upper
        if width <= atr * 1.4:
            return "CHOP"
        if 0.25 < position < 0.75:
            return "NO_TRADE"
        return "RANGE_BOUND"

    trend = trend_direction(df)
    if trend == "up":
        return "TRENDING_UP"
    if trend == "down":
        return "TRENDING_DOWN"
    return "CHOP"


def higher_timeframe_bias(htf_dfs: list[pd.DataFrame] | None) -> str:
    if not htf_dfs:
        return "HTF_NEUTRAL"
    votes = [trend_direction(df) for df in htf_dfs if len(df) >= 40]
    bullish = votes.count("up")
    bearish = votes.count("down")
    if bullish > bearish:
        return "HTF_BULLISH"
    if bearish > bullish:
        return "HTF_BEARISH"
    return "HTF_NEUTRAL"
