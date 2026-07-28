from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


TradeSide = Literal["BUY", "SELL", "LONG", "SHORT"]


@dataclass(frozen=True)
class ForexPipDistances:
    pip_size: float
    stop_loss_pips: float
    take_profit_1_pips: float
    take_profit_2_pips: float | None = None


def pip_size_for_symbol(symbol: str, provider_pip_size: float | None = None) -> float:
    if provider_pip_size and provider_pip_size > 0:
        return provider_pip_size
    normalized = symbol.upper().replace("/", "")
    if normalized.startswith("XAU") or normalized.endswith("XAU"):
        raise ValueError("Metals require provider or broker pip precision.")
    if "JPY" in normalized:
        return 0.01
    return 0.0001


def pip_distance(symbol: str, from_price: float | None, to_price: float | None, provider_pip_size: float | None = None) -> float:
    if from_price is None or to_price is None:
        raise ValueError("Both prices are required.")
    if from_price <= 0 or to_price <= 0:
        raise ValueError("Prices must be positive.")
    pip_size = pip_size_for_symbol(symbol, provider_pip_size)
    return round(abs(float(to_price) - float(from_price)) / pip_size, 1)


def trade_pip_distances(
    *,
    symbol: str,
    side: TradeSide,
    entry: float | None,
    stop_loss: float | None,
    take_profit_1: float | None,
    take_profit_2: float | None = None,
    provider_pip_size: float | None = None,
) -> ForexPipDistances:
    normalized_side = side.upper()
    if normalized_side not in {"BUY", "SELL", "LONG", "SHORT"}:
        raise ValueError("Unsupported trade side.")
    if entry is None or stop_loss is None or take_profit_1 is None:
        raise ValueError("Entry, stop loss, and TP1 are required.")
    if entry <= 0 or stop_loss <= 0 or take_profit_1 <= 0 or (take_profit_2 is not None and take_profit_2 <= 0):
        raise ValueError("Prices must be positive.")

    is_buy = normalized_side in {"BUY", "LONG"}
    if is_buy and not (stop_loss < entry < take_profit_1):
        raise ValueError("Buy trades require stop_loss < entry < take_profit_1.")
    if not is_buy and not (take_profit_1 < entry < stop_loss):
        raise ValueError("Sell trades require take_profit_1 < entry < stop_loss.")
    if take_profit_2 is not None:
        if is_buy and take_profit_2 <= take_profit_1:
            raise ValueError("Buy TP2 must be above TP1.")
        if not is_buy and take_profit_2 >= take_profit_1:
            raise ValueError("Sell TP2 must be below TP1.")

    pip_size = pip_size_for_symbol(symbol, provider_pip_size)
    return ForexPipDistances(
        pip_size=pip_size,
        stop_loss_pips=pip_distance(symbol, entry, stop_loss, provider_pip_size),
        take_profit_1_pips=pip_distance(symbol, entry, take_profit_1, provider_pip_size),
        take_profit_2_pips=pip_distance(symbol, entry, take_profit_2, provider_pip_size) if take_profit_2 is not None else None,
    )
