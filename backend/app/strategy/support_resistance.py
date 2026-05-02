import numpy as np
import pandas as pd

from app.models.schemas import Zone


def average_true_range(df: pd.DataFrame, period: int = 14) -> float:
    high_low = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift()).abs()
    low_close = (df["low"] - df["close"].shift()).abs()
    true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    atr = true_range.rolling(period).mean().iloc[-1]
    return float(atr if not np.isnan(atr) else high_low.tail(period).mean())


def detect_swings(df: pd.DataFrame, window: int = 3) -> pd.DataFrame:
    candles = df.copy()
    candles["swing_high"] = candles["high"] == candles["high"].rolling(window * 2 + 1, center=True).max()
    candles["swing_low"] = candles["low"] == candles["low"].rolling(window * 2 + 1, center=True).min()
    return candles


def _cluster_levels(levels: list[float], tolerance: float, zone_type: str) -> list[Zone]:
    if not levels:
        return []
    sorted_levels = sorted(levels)
    clusters: list[list[float]] = [[sorted_levels[0]]]
    for level in sorted_levels[1:]:
        center = float(np.mean(clusters[-1]))
        if abs(level - center) <= tolerance:
            clusters[-1].append(level)
        else:
            clusters.append([level])

    zones = []
    for cluster in clusters:
        center = float(np.mean(cluster))
        width = max(tolerance * 0.55, center * 0.0015)
        touches = len(cluster)
        zones.append(
            Zone(
                type=zone_type,
                lower=center - width,
                upper=center + width,
                strength=round(min(1.0, touches / 5), 2),
                touches=touches,
            )
        )
    return sorted(zones, key=lambda zone: zone.strength, reverse=True)


def find_support_resistance(df: pd.DataFrame, lookback: int = 180) -> tuple[list[Zone], list[Zone]]:
    recent = detect_swings(df.tail(lookback).reset_index(drop=True))
    atr = average_true_range(recent)
    tolerance = max(atr * 0.65, float(recent["close"].iloc[-1]) * 0.002)
    supports = _cluster_levels(recent.loc[recent["swing_low"], "low"].tolist(), tolerance, "support")
    resistances = _cluster_levels(recent.loc[recent["swing_high"], "high"].tolist(), tolerance, "resistance")
    return supports[:5], resistances[:5]


def nearest_range(current_price: float, supports: list[Zone], resistances: list[Zone]) -> tuple[Zone | None, Zone | None]:
    below = [zone for zone in supports if zone.upper <= current_price * 1.01]
    above = [zone for zone in resistances if zone.lower >= current_price * 0.99]
    support = min(below, key=lambda zone: abs(current_price - zone.upper), default=None)
    resistance = min(above, key=lambda zone: abs(zone.lower - current_price), default=None)
    if support is None and supports:
        support = min(supports, key=lambda zone: abs(current_price - (zone.lower + zone.upper) / 2))
    if resistance is None and resistances:
        resistance = min(resistances, key=lambda zone: abs(current_price - (zone.lower + zone.upper) / 2))
    return support, resistance
