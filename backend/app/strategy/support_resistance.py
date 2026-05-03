import numpy as np
import pandas as pd

from app.models.schemas import Zone


def average_true_range(df: pd.DataFrame, period: int = 14) -> float:
    high_low = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift()).abs()
    low_close = (df["low"] - df["close"].shift()).abs()
    true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    atr = true_range.rolling(period).mean().iloc[-1]
    fallback = high_low.tail(period).mean()
    return float(atr if not np.isnan(atr) else fallback)


def detect_swings(df: pd.DataFrame, window: int = 3) -> pd.DataFrame:
    candles = df.copy(deep=True)
    swing_high = candles["high"] == candles["high"].rolling(window * 2 + 1, center=True).max()
    swing_low = candles["low"] == candles["low"].rolling(window * 2 + 1, center=True).min()
    candles = candles.assign(swing_high=swing_high, swing_low=swing_low)
    return candles


def _volume_ratio(df: pd.DataFrame, idx: int, period: int = 20) -> float:
    start = max(0, idx - period)
    baseline = float(df["volume"].iloc[start:idx].mean() or df["volume"].mean() or 1)
    return float(df["volume"].iloc[idx]) / max(baseline, 1e-9)


def _reaction_strength(df: pd.DataFrame, idx: int, zone_type: str, atr: float, forward: int = 8) -> float:
    candle = df.iloc[idx]
    future = df.iloc[idx + 1 : idx + 1 + forward]
    if future.empty or atr <= 0:
        return 0.0

    if zone_type == "support":
        move = float(future["high"].max() - candle["low"])
        wick = float(min(candle["open"], candle["close"]) - candle["low"])
    else:
        move = float(candle["high"] - future["low"].min())
        wick = float(candle["high"] - max(candle["open"], candle["close"]))

    wick_score = min(1.0, max(0.0, wick / max(float(candle["high"] - candle["low"]), 1e-9)))
    move_score = min(1.0, max(0.0, move / (atr * 2.5)))
    volume_score = min(1.0, max(0.0, (_volume_ratio(df, idx) - 1) / 1.5))
    return move_score * 0.5 + wick_score * 0.3 + volume_score * 0.2


def _cluster_candidates(candidates: list[tuple[int, float]], tolerance: float) -> list[list[tuple[int, float]]]:
    if not candidates:
        return []
    ordered = sorted(candidates, key=lambda item: item[1])
    clusters: list[list[tuple[int, float]]] = [[ordered[0]]]
    for idx, level in ordered[1:]:
        center = float(np.mean([item[1] for item in clusters[-1]]))
        if abs(level - center) <= tolerance:
            clusters[-1].append((idx, level))
        else:
            clusters.append([(idx, level)])
    return clusters


def _score_zone(df: pd.DataFrame, cluster: list[tuple[int, float]], zone_type: str, tolerance: float, atr: float) -> Zone:
    levels = [level for _, level in cluster]
    indexes = [idx for idx, _ in cluster]
    center = float(np.mean(levels))
    width = max(tolerance * 0.65, center * 0.0015)
    lower = center - width
    upper = center + width

    touches = len(cluster)
    recency = 1 - min(1.0, (len(df) - max(indexes)) / max(len(df), 1))
    reactions = [_reaction_strength(df, idx, zone_type, atr) for idx in indexes]
    reaction_score = float(np.mean(reactions)) if reactions else 0.0
    volume_score = min(1.0, float(np.mean([_volume_ratio(df, idx) for idx in indexes])) / 2.2)
    touch_score = min(1.0, touches / 5)
    strength = round(min(1.0, touch_score * 0.35 + reaction_score * 0.35 + volume_score * 0.15 + recency * 0.15), 2)

    return Zone(
        type=zone_type,
        lower=round(lower, 8),
        upper=round(upper, 8),
        strength=strength,
        touches=touches,
        lower_bound=round(lower, 8),
        upper_bound=round(upper, 8),
        strength_score=round(strength * 100, 1),
        last_reaction_time=df.iloc[max(indexes)]["timestamp"],
        role="SUPPORT" if zone_type == "support" else "RESISTANCE",
    )


def find_support_resistance(df: pd.DataFrame, lookback: int = 220) -> tuple[list[Zone], list[Zone]]:
    recent = detect_swings(df.tail(lookback).reset_index(drop=True))
    atr = average_true_range(recent)
    current_price = float(recent["close"].iloc[-1])
    tolerance = max(atr * 0.45, current_price * 0.002)

    support_candidates = [(int(idx), float(row["low"])) for idx, row in recent.loc[recent["swing_low"]].iterrows()]
    resistance_candidates = [(int(idx), float(row["high"])) for idx, row in recent.loc[recent["swing_high"]].iterrows()]

    supports = [_score_zone(recent, cluster, "support", tolerance, atr) for cluster in _cluster_candidates(support_candidates, tolerance)]
    resistances = [_score_zone(recent, cluster, "resistance", tolerance, atr) for cluster in _cluster_candidates(resistance_candidates, tolerance)]

    supports = sorted(supports, key=lambda zone: (zone.strength, zone.touches), reverse=True)
    resistances = sorted(resistances, key=lambda zone: (zone.strength, zone.touches), reverse=True)
    return supports[:6], resistances[:6]


def nearest_range(current_price: float, supports: list[Zone], resistances: list[Zone]) -> tuple[Zone | None, Zone | None]:
    below = [zone for zone in supports if zone.upper <= current_price * 1.015]
    above = [zone for zone in resistances if zone.lower >= current_price * 0.985]
    support = min(below, key=lambda zone: abs(current_price - zone.upper), default=None)
    resistance = min(above, key=lambda zone: abs(zone.lower - current_price), default=None)

    if support is None and supports:
        support = min(supports, key=lambda zone: abs(current_price - (zone.lower + zone.upper) / 2))
    if resistance is None and resistances:
        resistance = min(resistances, key=lambda zone: abs(current_price - (zone.lower + zone.upper) / 2))
    return support, resistance
