from __future__ import annotations

import math
import pandas as pd


def infer_minimum_tick(frame: pd.DataFrame) -> float:
    """Conservative five-significant-figure tick fallback for crypto feeds."""
    if frame.empty:
        return 1e-8
    price = abs(float(frame["close"].astype(float).median()))
    if not math.isfinite(price) or price <= 0:
        return 1e-8
    return max(1e-8, 10 ** (math.floor(math.log10(price)) - 4))


def calculate_volume_profile(
    frame: pd.DataFrame,
    *,
    rows: int = 24,
    minimum_tick: float | None = None,
    value_area_percent: float = 70.0,
) -> dict:
    """Python service equivalent of SwiftChart's informational V2 profile."""
    if frame.empty or rows < 2 or not 50 <= value_area_percent <= 95:
        return {"available": False, "poc": None, "vah": None, "val": None, "delta_percent": None}

    recent = frame.copy()
    profile_low = float(recent["low"].astype(float).min())
    profile_high = float(recent["high"].astype(float).max())
    tick = float(minimum_tick or infer_minimum_tick(recent))
    if not math.isfinite(profile_low) or not math.isfinite(profile_high) or tick <= 0:
        return {"available": False, "poc": None, "vah": None, "val": None, "delta_percent": None}
    base = math.floor(profile_low / tick) * tick
    span = max(profile_high - base, tick)
    row_ticks = max(1, math.ceil(span / (rows * tick)))
    row_size = row_ticks * tick
    total_bins = [0.0] * rows
    up_bins = [0.0] * rows
    down_bins = [0.0] * rows
    for bar in recent.itertuples(index=False):
        bar_low = float(bar.low)
        bar_high = float(bar.high)
        try:
            volume = float(bar.volume or 0.0)
        except (TypeError, ValueError):
            volume = 0.0
        if not math.isfinite(volume) or volume <= 0:
            continue
        target = up_bins if float(bar.close) >= float(bar.open) else down_bins
        if bar_high <= bar_low:
            index = max(0, min(rows - 1, math.floor((float(bar.close) - base) / row_size)))
            total_bins[index] += volume
            target[index] += volume
            continue
        first = max(0, min(rows - 1, math.floor((bar_low - base) / row_size)))
        last = max(0, min(rows - 1, math.floor((bar_high - base) / row_size)))
        for index in range(first, last + 1):
            bin_low = base + index * row_size
            bin_high = bin_low + row_size
            overlap = max(0.0, min(bar_high, bin_high) - max(bar_low, bin_low))
            allocation = volume * overlap / (bar_high - bar_low)
            total_bins[index] += allocation
            target[index] += allocation

    profile_volume = float(sum(total_bins))
    if profile_volume <= 0:
        return {"available": False, "poc": None, "vah": None, "val": None, "delta_percent": None}
    profile_mid = (profile_low + profile_high) / 2
    maximum = max(total_bins)
    tied = [index for index, value in enumerate(total_bins) if value == maximum]
    poc_index = min(tied, key=lambda index: (abs(base + (index + 0.5) * row_size - profile_mid), -index))
    included = float(total_bins[poc_index])
    low_index = high_index = poc_index
    target_volume = profile_volume * value_area_percent / 100
    while included < target_volume and (low_index > 0 or high_index < rows - 1):
        lower_volume = total_bins[low_index - 1] if low_index > 0 else -1
        upper_volume = total_bins[high_index + 1] if high_index < rows - 1 else -1
        if upper_volume >= lower_volume and high_index < rows - 1:
            high_index += 1
            included += float(total_bins[high_index])
        elif low_index > 0:
            low_index -= 1
            included += float(total_bins[low_index])

    up_volume = float(sum(up_bins))
    down_volume = float(sum(down_bins))
    return {
        "available": True,
        "poc": base + (poc_index + 0.5) * row_size,
        "vah": base + (high_index + 1) * row_size,
        "val": base + low_index * row_size,
        "delta_percent": (up_volume - down_volume) / profile_volume * 100,
        "profile_volume": profile_volume,
        "rows": rows,
        "minimum_tick": tick,
        "value_area_percent": value_area_percent,
    }
