from __future__ import annotations

from execution_bot.config import ExecutionSettings
from execution_bot.indicators import recent_swing_high, recent_swing_low
from execution_bot.market_filter import atr_multiplier
from execution_bot.models import Candle, ExecutionPlan, SignalIn, SignalSide, TradeMode


def risk_percent_for_signal(confidence: float, consecutive_losses: int, settings: ExecutionSettings) -> float:
    risk_percent = settings.base_risk_percent
    if confidence >= 90:
        risk_percent = min(settings.max_risk_percent, max(risk_percent, 3))
    if confidence >= 95:
        risk_percent = min(settings.max_risk_percent, max(risk_percent, 5))
    if consecutive_losses >= 3:
        risk_percent *= 0.5
    return min(risk_percent, settings.max_risk_percent)


def structure_stop(signal: SignalIn, candles: list[Candle], atr_value: float) -> tuple[float, float]:
    buffer = atr_value * 0.15
    if signal.side == SignalSide.buy:
        level = recent_swing_low(candles)
        return level - buffer, level
    level = recent_swing_high(candles)
    return level + buffer, level


def atr_stop(signal: SignalIn, atr_value: float, atr_percent: float) -> float:
    distance = atr_value * atr_multiplier(atr_percent)
    if signal.side == SignalSide.buy:
        return signal.entry - distance
    return signal.entry + distance


def safer_stop(signal: SignalIn, atr_based_stop: float, structure_based_stop: float) -> float:
    if signal.side == SignalSide.buy:
        return min(atr_based_stop, structure_based_stop)
    return max(atr_based_stop, structure_based_stop)


def take_profit_levels(signal: SignalIn, stop_distance: float) -> list[dict[str, float]]:
    direction = 1 if signal.side == SignalSide.buy else -1
    return [
        {"target": signal.entry + direction * stop_distance * 1, "r_multiple": 1, "close_percent": 40},
        {"target": signal.entry + direction * stop_distance * 2, "r_multiple": 2, "close_percent": 30},
        {"target": signal.entry + direction * stop_distance * 3, "r_multiple": 3, "close_percent": 30},
    ]


def build_execution_plan(
    signal: SignalIn,
    candles: list[Candle],
    account_balance: float,
    consecutive_losses: int,
    open_exposure: float,
    atr_value: float,
    atr_percent: float,
    market_condition: str,
    settings: ExecutionSettings,
    base_risk_percent: float | None = None,
) -> ExecutionPlan:
    if base_risk_percent is not None:
        settings = settings.model_copy(update={"base_risk_percent": base_risk_percent})
    risk_percent = risk_percent_for_signal(signal.confidence, consecutive_losses, settings)
    atr_based_stop = atr_stop(signal, atr_value, atr_percent)
    structure_based_stop, structure_level = structure_stop(signal, candles, atr_value)
    stop_loss = safer_stop(signal, atr_based_stop, structure_based_stop)
    stop_distance = abs(signal.entry - stop_loss)
    if stop_distance <= 0:
        raise ValueError("Stop distance must be positive.")

    risk_amount = account_balance * (risk_percent / 100)
    position_size = risk_amount / stop_distance
    notional_value = position_size * signal.entry

    max_notional_by_leverage = account_balance * settings.max_leverage
    max_notional_by_coin = account_balance * (settings.max_exposure_per_coin_percent / 100)
    available_coin_notional = max(0, max_notional_by_coin - open_exposure)
    allowed_notional = min(max_notional_by_leverage, available_coin_notional)
    if allowed_notional <= 0:
        raise ValueError("Maximum exposure for this coin is already reached.")

    notes: list[str] = []
    if notional_value > allowed_notional:
        notional_value = allowed_notional
        position_size = notional_value / signal.entry
        risk_amount = position_size * stop_distance
        risk_percent = (risk_amount / account_balance) * 100 if account_balance else 0
        notes.append("Position size reduced to stay within leverage/exposure limits.")

    leverage = max(1.0, min(settings.max_leverage, notional_value / account_balance if account_balance else 1))

    return ExecutionPlan(
        signal=signal,
        symbol=f"{signal.pair}{settings.execution_quote_asset}" if not signal.pair.endswith(settings.execution_quote_asset) else signal.pair,
        side=signal.side,
        entry=signal.entry,
        stop_loss=stop_loss,
        stop_distance=stop_distance,
        atr=atr_value,
        atr_percent=atr_percent,
        structure_level=structure_level,
        risk_percent=risk_percent,
        risk_amount=risk_amount,
        position_size=position_size,
        notional_value=notional_value,
        leverage=leverage,
        take_profits=take_profit_levels(signal, stop_distance),
        market_condition=market_condition,
        mode=TradeMode.live if settings.live_enabled else TradeMode.paper,
        notes=notes,
    )
