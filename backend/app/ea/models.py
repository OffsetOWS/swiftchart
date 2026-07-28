from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from app.mt5.models import ForexAutoSignal, RiskLimits, SignalValidationStatus, ValidationResult


class EAExecutionState(str, Enum):
    received = "received"
    executing = "executing"
    executed = "executed"
    rejected = "rejected"
    partially_closed = "partially_closed"
    breakeven_moved = "breakeven_moved"
    trailing_updated = "trailing_updated"
    closed = "closed"
    failed = "failed"


class EAPendingSignal(BaseModel):
    trade_id: str
    signal: ForexAutoSignal
    status: EAExecutionState = EAExecutionState.received
    validation: ValidationResult
    created_at: datetime
    updated_at: datetime
    fetched_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class EASignalQueueResponse(BaseModel):
    accepted: bool
    message: str
    signal: EAPendingSignal | None = None
    validation: ValidationResult
    dry_run: bool = False


class EAPendingSignalsResponse(BaseModel):
    signals: list[EAPendingSignal]


class EATradeUpdateRequest(BaseModel):
    trade_id: str = Field(..., min_length=3, max_length=120)
    status: EAExecutionState
    message: str | None = Field(default=None, max_length=1000)
    broker_order_id: str | None = Field(default=None, max_length=120)
    broker_position_id: str | None = Field(default=None, max_length=120)
    executed_price: float | None = Field(default=None, gt=0)
    executed_volume: float | None = Field(default=None, gt=0)
    stop_loss: float | None = Field(default=None, gt=0)
    take_profit: float | None = Field(default=None, gt=0)
    pnl: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class EATradeUpdateResponse(BaseModel):
    accepted: bool
    message: str
    signal: EAPendingSignal | None = None


class EAHeartbeatRequest(BaseModel):
    client_id: str = Field(default="default", min_length=1, max_length=120)
    terminal_id: str | None = Field(default=None, max_length=120)
    ea_version: str | None = Field(default=None, max_length=80)
    broker_name: str | None = Field(default=None, max_length=160)
    account_currency: str = Field(default="USD", min_length=3, max_length=8)
    balance: float | None = Field(default=None, ge=0)
    equity: float | None = Field(default=None, ge=0)
    margin_free: float | None = Field(default=None, ge=0)
    trading_allowed: bool = True
    open_positions: int = Field(default=0, ge=0)
    last_error: str | None = Field(default=None, max_length=1000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class EAHeartbeatResponse(BaseModel):
    accepted: bool
    message: str
    server_time: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class EAConfigResponse(BaseModel):
    mode: str = "mql5_ea"
    production_execution_path: str = "mql5_expert_advisor"
    poll_interval_seconds: int
    idle_poll_interval_seconds: int
    error_retry_interval_seconds: int
    active_poll_interval_seconds: int
    max_signals_per_poll: int
    risk: RiskLimits
    trade_update_states: list[EAExecutionState]
    python_mt5_bridge: str = "legacy_optional"
