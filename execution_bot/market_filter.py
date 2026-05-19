from __future__ import annotations

from pydantic import BaseModel

from execution_bot.config import ExecutionSettings
from execution_bot.indicators import adx, atr, average_volume
from execution_bot.models import Candle, MarketSnapshot


class MarketFilterResult(BaseModel):
    allowed: bool
    condition: str
    reasons: list[str]
    atr_value: float
    atr_percent: float
    adx_value: float
    spread_percent: float
    volume_ratio: float


def classify_volatility(atr_percent: float) -> str:
    if atr_percent < 0.35:
        return "low"
    if atr_percent > 1.5:
        return "high"
    return "normal"


def atr_multiplier(atr_percent: float) -> float:
    volatility = classify_volatility(atr_percent)
    if volatility == "low":
        return 1.2
    if volatility == "high":
        return 2.0
    return 1.5


def evaluate_market(snapshot: MarketSnapshot, settings: ExecutionSettings) -> MarketFilterResult:
    candles = snapshot.candles
    if len(candles) < 30:
        return MarketFilterResult(
            allowed=False,
            condition="insufficient-data",
            reasons=["Need at least 30 candles for execution filters."],
            atr_value=0,
            atr_percent=0,
            adx_value=0,
            spread_percent=0,
            volume_ratio=0,
        )

    closed_candles = candles[:-1] if len(candles) > 30 else candles
    signal_candle = closed_candles[-1]
    last_close = signal_candle.close
    atr_value = atr(closed_candles, 14)
    atr_percent = (atr_value / last_close) * 100 if last_close else 0
    adx_value = adx(closed_candles, 14)
    avg_volume = average_volume(closed_candles[:-1], 20)
    volume_ratio = signal_candle.volume / avg_volume if avg_volume else 1

    spread_percent = 0.0
    if snapshot.bid and snapshot.ask and snapshot.ask > snapshot.bid:
        mid = (snapshot.ask + snapshot.bid) / 2
        spread_percent = ((snapshot.ask - snapshot.bid) / mid) * 100

    recent = closed_candles[-20:]
    range_percent = ((max(c.high for c in recent) - min(c.low for c in recent)) / last_close) * 100
    reasons: list[str] = []

    if adx_value < 20:
        reasons.append("ADX below 20: market is too weak or choppy.")
    if atr_percent < settings.min_atr_percent:
        reasons.append("ATR percent too low: compression is too tight.")
    if atr_percent > settings.max_atr_percent:
        reasons.append("ATR percent too high: volatility danger.")
    if range_percent < settings.min_atr_percent * 3:
        reasons.append("Price is stuck in a tight range.")
    if volume_ratio < settings.min_volume_ratio:
        reasons.append("Volume is weak.")
    if spread_percent > settings.max_spread_percent:
        reasons.append("Spread/slippage is elevated.")

    if atr_percent > settings.max_atr_percent:
        condition = "high-volatility-danger"
    elif atr_percent < settings.min_atr_percent or range_percent < settings.min_atr_percent * 3:
        condition = "compression"
    elif adx_value < 20:
        condition = "choppy"
    elif range_percent < atr_percent * 4:
        condition = "ranging"
    else:
        condition = "trending"

    return MarketFilterResult(
        allowed=True,
        condition=condition,
        reasons=reasons,
        atr_value=atr_value,
        atr_percent=atr_percent,
        adx_value=adx_value,
        spread_percent=spread_percent,
        volume_ratio=volume_ratio,
    )
