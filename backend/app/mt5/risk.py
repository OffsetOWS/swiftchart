from __future__ import annotations

from app.forex.config import SUPPORTED_FOREX_PAIRS
from app.mt5.models import ForexAutoSignal, PositionSizingResult, RiskLimits, SymbolSnapshot


def pip_size_for_pair(pair: str, symbol: SymbolSnapshot | None = None) -> float:
    normalized = pair.upper().replace("/", "")
    if symbol is not None:
        return symbol.pip_size
    configured = SUPPORTED_FOREX_PAIRS.get(normalized)
    if configured:
        return configured.pip_size
    if "JPY" in normalized:
        return 0.01
    if normalized.startswith("XAU"):
        return 0.1
    return 0.0001


def stop_loss_pips(signal: ForexAutoSignal, symbol: SymbolSnapshot | None = None) -> float:
    pip_size = pip_size_for_pair(signal.pair, symbol)
    return abs(signal.entry - signal.stop_loss) / pip_size


def round_volume_to_step(volume: float, step: float) -> float:
    if step <= 0:
        return round(volume, 2)
    steps = round(volume / step)
    decimals = max(0, len(f"{step:.10f}".rstrip("0").split(".")[-1]))
    return round(steps * step, decimals)


def calculate_lot_size(
    signal: ForexAutoSignal,
    *,
    balance: float,
    equity: float,
    symbol: SymbolSnapshot,
    limits: RiskLimits,
    current_total_lots: float = 0,
) -> PositionSizingResult:
    risk_percent = signal.risk_percent or limits.risk_per_trade_percent
    risk_base = min(value for value in (balance, equity) if value > 0)
    risk_amount = risk_base * (risk_percent / 100)
    sl_pips = stop_loss_pips(signal, symbol)
    notes: list[str] = []
    if sl_pips <= 0:
        raise ValueError("Stop loss distance must be positive.")
    if symbol.pip_value_per_lot <= 0:
        raise ValueError("Pip value must be positive.")

    raw_lots = risk_amount / (sl_pips * symbol.pip_value_per_lot)
    max_remaining_lots = max(0, limits.maximum_total_lots - current_total_lots)
    allowed_max = min(limits.maximum_lot, symbol.volume_max, max_remaining_lots)
    allowed_min = max(limits.minimum_lot, symbol.volume_min)
    clamped = False

    if allowed_max <= 0:
        raise ValueError("Maximum total lots limit has been reached.")
    lot_size = raw_lots
    if lot_size < allowed_min:
        lot_size = allowed_min
        clamped = True
        notes.append("Lot size increased to broker/configured minimum.")
    if lot_size > allowed_max:
        lot_size = allowed_max
        clamped = True
        notes.append("Lot size reduced to stay within maximum lot exposure.")

    lot_size = round_volume_to_step(lot_size, symbol.volume_step)
    if lot_size <= 0:
        raise ValueError("Calculated lot size is invalid.")

    return PositionSizingResult(
        lot_size=lot_size,
        risk_amount=risk_amount,
        stop_loss_pips=sl_pips,
        pip_value_per_lot=symbol.pip_value_per_lot,
        clamped=clamped,
        notes=notes,
    )

