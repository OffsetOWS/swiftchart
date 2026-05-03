import pandas as pd

from app.models.schemas import LiquiditySweep, Zone


def _volume_spike(df: pd.DataFrame, idx: int, period: int = 20) -> bool:
    start = max(0, idx - period)
    baseline = float(df["volume"].iloc[start:idx].mean() or df["volume"].mean() or 1)
    return float(df["volume"].iloc[idx]) >= baseline * 1.2


def _bullish_confirmation(df: pd.DataFrame, idx: int, support: Zone) -> tuple[bool, float]:
    if idx + 1 >= len(df):
        return False, 0.0
    sweep = df.iloc[idx]
    confirm = df.iloc[idx + 1]
    closed_inside = float(sweep["close"]) > support.upper
    reclaimed = float(confirm["close"]) > max(float(sweep["close"]), support.upper)
    failed_continuation = float(confirm["low"]) > float(sweep["low"])
    momentum = float(confirm["close"]) > float(confirm["open"])
    volume = _volume_spike(df, idx) or _volume_spike(df, idx + 1)
    score = 45 + (20 if reclaimed else 0) + (15 if failed_continuation else 0) + (10 if momentum else 0) + (10 if volume else 0)
    return closed_inside and reclaimed and failed_continuation, min(100.0, score)


def _bearish_confirmation(df: pd.DataFrame, idx: int, resistance: Zone) -> tuple[bool, float]:
    if idx + 1 >= len(df):
        return False, 0.0
    sweep = df.iloc[idx]
    confirm = df.iloc[idx + 1]
    closed_inside = float(sweep["close"]) < resistance.lower
    rejected = float(confirm["close"]) < min(float(sweep["close"]), resistance.lower)
    failed_continuation = float(confirm["high"]) < float(sweep["high"])
    momentum = float(confirm["close"]) < float(confirm["open"])
    volume = _volume_spike(df, idx) or _volume_spike(df, idx + 1)
    score = 45 + (20 if rejected else 0) + (15 if failed_continuation else 0) + (10 if momentum else 0) + (10 if volume else 0)
    return closed_inside and rejected and failed_continuation, min(100.0, score)


def detect_liquidity_sweeps(df: pd.DataFrame, supports: list[Zone], resistances: list[Zone], lookback: int = 36) -> list[LiquiditySweep]:
    start = max(0, len(df) - lookback)
    sweeps: list[LiquiditySweep] = []

    for idx in range(start, len(df) - 1):
        candle = df.iloc[idx]
        for support in supports[:4]:
            swept = float(candle["low"]) < support.lower and float(candle["close"]) > support.upper
            if swept:
                confirmed, quality = _bullish_confirmation(df, idx, support)
                depth = (support.lower - float(candle["low"])) / max(support.lower, 1e-9)
                quality = min(100.0, quality + min(15.0, depth * 1000))
                sweeps.append(
                    LiquiditySweep(
                        direction="bullish",
                        swept_level=support.lower,
                        candle_time=candle["timestamp"],
                        reclaim_price=float(candle["close"]),
                        strength=round(quality / 100, 2),
                        sweep_direction="bullish",
                        confirmation_status="confirmed" if confirmed else "unconfirmed",
                        sweep_quality_score=round(quality, 1),
                    )
                )

        for resistance in resistances[:4]:
            swept = float(candle["high"]) > resistance.upper and float(candle["close"]) < resistance.lower
            if swept:
                confirmed, quality = _bearish_confirmation(df, idx, resistance)
                depth = (float(candle["high"]) - resistance.upper) / max(resistance.upper, 1e-9)
                quality = min(100.0, quality + min(15.0, depth * 1000))
                sweeps.append(
                    LiquiditySweep(
                        direction="bearish",
                        swept_level=resistance.upper,
                        candle_time=candle["timestamp"],
                        reclaim_price=float(candle["close"]),
                        strength=round(quality / 100, 2),
                        sweep_direction="bearish",
                        confirmation_status="confirmed" if confirmed else "unconfirmed",
                        sweep_quality_score=round(quality, 1),
                    )
                )

    return sweeps[-8:]
