from __future__ import annotations

from statistics import mean

from execution_bot.models import Candle


def true_ranges(candles: list[Candle]) -> list[float]:
    ranges: list[float] = []
    previous_close: float | None = None
    for candle in candles:
        if previous_close is None:
            ranges.append(candle.high - candle.low)
        else:
            ranges.append(max(candle.high - candle.low, abs(candle.high - previous_close), abs(candle.low - previous_close)))
        previous_close = candle.close
    return ranges


def atr(candles: list[Candle], period: int = 14) -> float:
    if len(candles) < period + 1:
        raise ValueError(f"Need at least {period + 1} candles to calculate ATR.")
    values = true_ranges(candles)[-period:]
    return mean(values)


def adx(candles: list[Candle], period: int = 14) -> float:
    if len(candles) < period + 2:
        return 0
    plus_dm: list[float] = []
    minus_dm: list[float] = []
    trs = true_ranges(candles)
    for index in range(1, len(candles)):
        up_move = candles[index].high - candles[index - 1].high
        down_move = candles[index - 1].low - candles[index].low
        plus_dm.append(up_move if up_move > down_move and up_move > 0 else 0)
        minus_dm.append(down_move if down_move > up_move and down_move > 0 else 0)
    recent_tr = sum(trs[-period:]) or 1
    plus_di = 100 * (sum(plus_dm[-period:]) / recent_tr)
    minus_di = 100 * (sum(minus_dm[-period:]) / recent_tr)
    denominator = plus_di + minus_di
    if denominator == 0:
        return 0
    return 100 * abs(plus_di - minus_di) / denominator


def recent_swing_low(candles: list[Candle], lookback: int = 12) -> float:
    recent = candles[-lookback:]
    return min(candle.low for candle in recent)


def recent_swing_high(candles: list[Candle], lookback: int = 12) -> float:
    recent = candles[-lookback:]
    return max(candle.high for candle in recent)


def average_volume(candles: list[Candle], period: int = 20) -> float:
    recent = candles[-period:]
    if not recent:
        return 0
    return mean(candle.volume for candle in recent)
