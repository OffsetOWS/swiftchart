from __future__ import annotations

from app.config import get_settings
from app.ea import storage
from app.ea.models import (
    EAConfigResponse,
    EAExecutionState,
    EAHeartbeatRequest,
    EAHeartbeatResponse,
    EAPendingSignalsResponse,
    EASignalQueueResponse,
    EATradeUpdateRequest,
    EATradeUpdateResponse,
)
from app.forex.sessions import forex_session_state
from app.mt5.models import ForexAutoSignal, RiskLimits, SignalValidationStatus, ValidationResult


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


class EAExecutionService:
    def validate_signal(self, signal: ForexAutoSignal) -> ValidationResult:
        limits = risk_limits_from_settings()
        reasons: list[str] = []
        if signal.confidence < limits.minimum_confidence:
            reasons.append(f"Confidence {signal.confidence:.0f} is below minimum {limits.minimum_confidence:.0f}.")
        if storage.pending_signal_exists(signal.trade_id):
            reasons.append("Duplicate trade ID.")
        if not forex_session_state().market_open:
            reasons.append("Forex market is currently closed.")
        if storage.today_trade_count() >= limits.maximum_trades_per_day:
            reasons.append("Daily trade limit reached.")
        if storage.open_trade_count() >= limits.maximum_open_trades:
            reasons.append("Maximum open trades reached.")
        if limits.one_trade_per_pair and storage.open_trade_count(signal.pair) > 0:
            reasons.append(f"{signal.pair} already has an active EA trade.")
        return ValidationResult(
            status=SignalValidationStatus.rejected if reasons else SignalValidationStatus.accepted,
            accepted=not reasons,
            reasons=reasons,
        )

    def receive_signal(self, signal: ForexAutoSignal, *, dry_run: bool = False) -> EASignalQueueResponse:
        validation = self.validate_signal(signal)
        if not validation.accepted:
            return EASignalQueueResponse(accepted=False, message="Signal rejected.", validation=validation, dry_run=dry_run)
        if dry_run:
            return EASignalQueueResponse(accepted=True, message="Signal validated in dry-run mode.", validation=validation, dry_run=True)
        queued = storage.queue_signal(signal, validation, metadata={"execution_path": "mql5_ea"})
        return EASignalQueueResponse(accepted=True, message="Signal queued for MQL5 EA.", signal=queued, validation=validation)

    def pending_signals(self, *, limit: int = 20) -> EAPendingSignalsResponse:
        return EAPendingSignalsResponse(signals=storage.fetch_pending_signals(limit=limit))

    def trade_update(self, update: EATradeUpdateRequest, api_key_hash: str) -> EATradeUpdateResponse:
        signal = storage.save_trade_update(update, api_key_hash=api_key_hash)
        if signal is None:
            return EATradeUpdateResponse(accepted=False, message="Trade ID was not found.")
        return EATradeUpdateResponse(accepted=True, message="Trade update recorded.", signal=signal)

    def heartbeat(self, heartbeat: EAHeartbeatRequest, api_key_hash: str) -> EAHeartbeatResponse:
        storage.save_heartbeat(api_key_hash, heartbeat)
        return EAHeartbeatResponse(accepted=True, message="Heartbeat recorded.")

    def config(self) -> EAConfigResponse:
        settings = get_settings()
        return EAConfigResponse(
            poll_interval_seconds=settings.ea_poll_interval_seconds,
            idle_poll_interval_seconds=settings.ea_idle_poll_interval_seconds,
            error_retry_interval_seconds=settings.ea_error_retry_interval_seconds,
            active_poll_interval_seconds=settings.ea_active_poll_interval_seconds,
            max_signals_per_poll=settings.ea_max_signals_per_poll,
            risk=risk_limits_from_settings(),
            trade_update_states=list(EAExecutionState),
        )
