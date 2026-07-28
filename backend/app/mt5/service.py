from __future__ import annotations

from datetime import datetime, timezone

from app.config import get_settings
from app.forex.sessions import forex_session_state
from app.mt5.bridge import MT5Bridge, MT5BridgeError
from app.mt5.models import (
    CloseTradeRequest,
    ForexAutoSignal,
    ForexTradeSide,
    MT5ConnectRequest,
    RiskLimits,
    SignalValidationStatus,
    TradeActionResponse,
    TradeEvent,
    TradeRecord,
    TradeStatus,
    ValidationResult,
)
from app.mt5.risk import calculate_lot_size
from app.mt5 import storage


def risk_limits_from_settings() -> RiskLimits:
    settings = get_settings()
    return RiskLimits(
        minimum_lot=settings.mt5_minimum_lot,
        maximum_lot=settings.mt5_maximum_lot,
        maximum_total_lots=settings.mt5_maximum_total_lots,
        risk_per_trade_percent=settings.mt5_risk_per_trade_percent,
        maximum_daily_loss_percent=settings.mt5_maximum_daily_loss_percent,
        maximum_daily_profit_percent=settings.mt5_maximum_daily_profit_percent,
        maximum_trades_per_day=settings.mt5_maximum_trades_per_day,
        maximum_open_trades=settings.mt5_maximum_open_trades,
        maximum_spread_pips=settings.mt5_maximum_spread_pips,
        minimum_confidence=settings.mt5_minimum_confidence,
        one_trade_per_pair=settings.mt5_one_trade_per_pair,
        break_even_trigger_percent=settings.mt5_break_even_trigger_percent,
        break_even_buffer_pips=settings.mt5_break_even_buffer_pips,
        partial_close_percent=settings.mt5_partial_close_percent,
        trailing_distance_pips=settings.mt5_trailing_distance_pips,
    )


class ForexExecutionService:
    def __init__(self, bridge: MT5Bridge) -> None:
        self.bridge = bridge

    def connect(self, request: MT5ConnectRequest):
        snapshot = self.bridge.connect(request)
        storage.save_account_snapshot(snapshot)
        return snapshot

    def account(self):
        try:
            snapshot = self.bridge.account()
            storage.save_account_snapshot(snapshot)
            return snapshot
        except MT5BridgeError:
            snapshot = storage.latest_account_snapshot()
            if snapshot:
                return snapshot.model_copy(update={"connected": False})
            raise

    def validate_signal(self, signal: ForexAutoSignal) -> ValidationResult:
        limits = risk_limits_from_settings()
        reasons: list[str] = []
        account = None
        symbol = None
        sizing = None

        if signal.confidence < limits.minimum_confidence:
            reasons.append(f"Confidence {signal.confidence:.0f} is below minimum {limits.minimum_confidence:.0f}.")
        if storage.duplicate_signal_exists(signal.trade_id):
            reasons.append("Duplicate trade ID.")
        if not forex_session_state().market_open:
            reasons.append("Forex market is currently closed.")
        if storage.today_trade_count() >= limits.maximum_trades_per_day:
            reasons.append("Daily trade limit reached.")
        if storage.open_trade_count() >= limits.maximum_open_trades:
            reasons.append("Maximum open trades reached.")
        if limits.one_trade_per_pair and storage.open_trade_count(signal.pair) > 0:
            reasons.append(f"{signal.pair} already has an open trade.")

        try:
            account = self.bridge.account()
            storage.save_account_snapshot(account)
            if not account.trade_allowed:
                reasons.append("MT5 account does not allow trading.")
            symbol = self.bridge.symbol(signal.pair)
            if not symbol.trade_allowed:
                reasons.append(f"{signal.pair} is not tradeable in MT5.")
            if symbol.spread_pips > limits.maximum_spread_pips:
                reasons.append(f"Spread {symbol.spread_pips:.1f} pips exceeds maximum {limits.maximum_spread_pips:.1f}.")
            sizing = calculate_lot_size(
                signal,
                balance=account.balance,
                equity=account.equity,
                symbol=symbol,
                limits=limits,
                current_total_lots=storage.current_total_lots(),
            )
            if sizing.lot_size <= 0:
                reasons.append("Invalid position size.")
            estimated_margin = signal.entry * sizing.lot_size * symbol.contract_size / max(account.leverage or 1, 1)
            if estimated_margin > account.margin_free:
                reasons.append("Insufficient free margin for calculated position size.")
            pnl = storage.today_pnl()
            if account.balance > 0 and pnl <= -(account.balance * limits.maximum_daily_loss_percent / 100):
                reasons.append("Maximum daily loss reached.")
            if account.balance > 0 and pnl >= account.balance * limits.maximum_daily_profit_percent / 100:
                reasons.append("Maximum daily profit reached.")
        except (MT5BridgeError, ValueError) as exc:
            reasons.append(str(exc))

        return ValidationResult(
            status=SignalValidationStatus.rejected if reasons else SignalValidationStatus.accepted,
            accepted=not reasons,
            reasons=reasons,
            sizing=sizing,
            account=account,
            symbol=symbol,
        )

    def accept_signal(self, signal: ForexAutoSignal) -> ValidationResult:
        validation = self.validate_signal(signal)
        if not validation.accepted:
            storage.add_trade_event(TradeEvent(trade_id=signal.trade_id, event_type="REJECTED", message="; ".join(validation.reasons)))
        return validation

    def open_trade(self, signal: ForexAutoSignal, dry_run: bool = False) -> TradeActionResponse:
        validation = self.validate_signal(signal)
        if not validation.accepted or validation.sizing is None:
            return TradeActionResponse(accepted=False, message="Trade rejected.", validation=validation)

        risk_percent = signal.risk_percent or risk_limits_from_settings().risk_per_trade_percent
        record = storage.create_trade_record(signal, validation.sizing.lot_size, risk_percent)
        storage.add_trade_event(TradeEvent(trade_id=signal.trade_id, event_type="OPEN_REQUESTED", message="Trade passed validation."))
        if dry_run:
            return TradeActionResponse(accepted=True, message="Trade validated in dry-run mode.", trade=record, validation=validation)

        result = self.bridge.open_market_order(signal, validation.sizing.lot_size)
        if not result.success:
            record.status = TradeStatus.failed
            record.close_reason = result.message
            storage.upsert_trade(record)
            storage.add_trade_event(TradeEvent(trade_id=signal.trade_id, event_type="FAILED", message=result.message))
            return TradeActionResponse(accepted=False, message=result.message, trade=record, validation=validation)

        record.status = TradeStatus.open
        record.mt5_order_id = result.order_id
        record.mt5_position_id = result.position_id
        record.opened_at = datetime.now(timezone.utc)
        record.metadata.update({"executed_price": result.executed_price, "retcode": result.retcode})
        storage.upsert_trade(record)
        storage.add_trade_event(TradeEvent(trade_id=signal.trade_id, event_type="OPENED", message="MT5 market order opened."))
        return TradeActionResponse(accepted=True, message="Trade opened.", trade=record, validation=validation)

    def close_trade(self, request: CloseTradeRequest) -> TradeActionResponse:
        record = storage.get_trade(request.trade_id)
        if record is None:
            return TradeActionResponse(accepted=False, message="Trade not found.")
        result = self.bridge.close_position(request, record.mt5_position_id)
        if not result.success:
            return TradeActionResponse(accepted=False, message=result.message, trade=record)
        record.status = TradeStatus.closed
        record.closed_at = datetime.now(timezone.utc)
        record.close_reason = request.reason
        storage.upsert_trade(record)
        storage.add_trade_event(TradeEvent(trade_id=record.trade_id, event_type="CLOSED", message=request.reason))
        return TradeActionResponse(accepted=True, message="Trade closed.", trade=record)

    def monitor_once(self) -> list[TradeRecord]:
        limits = risk_limits_from_settings()
        managed: list[TradeRecord] = []
        positions = {str(position.get("ticket")): position for position in self.bridge.positions()}
        active_trades = storage.list_trades(status=TradeStatus.open.value, limit=500)
        active_trades.extend(storage.list_trades(status=TradeStatus.partially_closed.value, limit=500))
        for trade in active_trades:
            if trade.mt5_position_id is None:
                continue
            position = positions.get(str(trade.mt5_position_id))
            if not position:
                continue
            current_price = float(position.get("price_current") or trade.entry)
            target_distance = abs(trade.tp1 - trade.entry)
            favorable_move = current_price - trade.entry if trade.side.value == "BUY" else trade.entry - current_price
            metadata = dict(trade.metadata)
            symbol = self.bridge.symbol(trade.pair)
            if not metadata.get("break_even_moved") and target_distance > 0 and favorable_move >= target_distance * limits.break_even_trigger_percent:
                buffer = limits.break_even_buffer_pips * symbol.pip_size
                new_sl = trade.entry + buffer if trade.side.value == "BUY" else trade.entry - buffer
                should_move = new_sl > trade.stop_loss if trade.side.value == "BUY" else new_sl < trade.stop_loss
                if should_move:
                    result = self.bridge.modify_stop_loss(trade.mt5_position_id, new_sl, trade.tp2 or trade.tp1)
                    if result.success:
                        trade.stop_loss = new_sl
                        metadata["break_even_moved"] = True
                        trade.metadata = metadata
                        storage.upsert_trade(trade)
                        storage.add_trade_event(TradeEvent(trade_id=trade.trade_id, event_type="BREAK_EVEN_MOVED", message="Stop moved to break-even."))
                        managed.append(trade)
            tp1_reached = current_price >= trade.tp1 if trade.side == ForexTradeSide.buy else current_price <= trade.tp1
            if tp1_reached and not metadata.get("partial_tp_closed"):
                partial_volume = max(0.0, trade.lot_size * (limits.partial_close_percent / 100))
                if partial_volume > 0:
                    result = self.bridge.close_position(
                        CloseTradeRequest(trade_id=trade.trade_id, volume=partial_volume, reason="tp1_partial"),
                        trade.mt5_position_id,
                    )
                    if result.success:
                        metadata["partial_tp_closed"] = True
                        trade.status = TradeStatus.partially_closed
                        trade.metadata = metadata
                        storage.upsert_trade(trade)
                        storage.add_trade_event(
                            TradeEvent(
                                trade_id=trade.trade_id,
                                event_type="PARTIAL_TP_CLOSED",
                                message=f"Closed {limits.partial_close_percent:.0f}% at TP1.",
                                metadata={"volume": partial_volume},
                            )
                        )
                        managed.append(trade)
            if metadata.get("break_even_moved") and trade.tp2 is None:
                trailing_distance = limits.trailing_distance_pips * symbol.pip_size
                trailed_sl = current_price - trailing_distance if trade.side == ForexTradeSide.buy else current_price + trailing_distance
                improves_stop = trailed_sl > trade.stop_loss if trade.side == ForexTradeSide.buy else trailed_sl < trade.stop_loss
                if improves_stop:
                    result = self.bridge.modify_stop_loss(trade.mt5_position_id, trailed_sl, trade.tp1)
                    if result.success:
                        trade.stop_loss = trailed_sl
                        trade.metadata = metadata
                        storage.upsert_trade(trade)
                        storage.add_trade_event(TradeEvent(trade_id=trade.trade_id, event_type="TRAILING_STOP_MOVED", message="Trailing stop advanced."))
                        managed.append(trade)
        return managed
