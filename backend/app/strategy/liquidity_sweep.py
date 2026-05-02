import pandas as pd

from app.models.schemas import LiquiditySweep, Zone


def detect_liquidity_sweeps(df: pd.DataFrame, supports: list[Zone], resistances: list[Zone], lookback: int = 30) -> list[LiquiditySweep]:
    recent = df.tail(lookback)
    sweeps: list[LiquiditySweep] = []

    for _, candle in recent.iterrows():
        for support in supports[:3]:
            swept = candle["low"] < support.lower and candle["close"] > support.upper
            if swept:
                depth = (support.lower - candle["low"]) / max(support.lower, 1e-9)
                sweeps.append(
                    LiquiditySweep(
                        direction="bullish",
                        swept_level=support.lower,
                        candle_time=candle["timestamp"],
                        reclaim_price=candle["close"],
                        strength=round(min(1.0, 0.55 + depth * 100), 2),
                    )
                )
        for resistance in resistances[:3]:
            swept = candle["high"] > resistance.upper and candle["close"] < resistance.lower
            if swept:
                depth = (candle["high"] - resistance.upper) / max(resistance.upper, 1e-9)
                sweeps.append(
                    LiquiditySweep(
                        direction="bearish",
                        swept_level=resistance.upper,
                        candle_time=candle["timestamp"],
                        reclaim_price=candle["close"],
                        strength=round(min(1.0, 0.55 + depth * 100), 2),
                    )
                )

    return sweeps[-6:]
