from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator


class ForexTradeSide(str, Enum):
    buy = "BUY"
    sell = "SELL"


class TradeStatus(str, Enum):
    pending = "PENDING"
    open = "OPEN"
    partially_closed = "PARTIALLY_CLOSED"
    closed = "CLOSED"
    rejected = "REJECTED"
    failed = "FAILED"


class SignalValidationStatus(str, Enum):
    accepted = "ACCEPTED"
    rejected = "REJECTED"


class ForexAutoSignal(BaseModel):
    pair: str = Field(..., min_length=3, max_length=20)
    side: ForexTradeSide
    timeframe: str = Field(..., min_length=1, max_length=16)
    entry: float = Field(..., gt=0)
    stop_loss: float = Field(..., gt=0)
    tp1: float = Field(..., gt=0)
    tp2: float | None = Field(default=None, gt=0)
    confidence: float = Field(..., ge=0, le=100)
    setup_score: float | None = Field(default=None, ge=0, le=100)
    risk_percent: float | None = Field(default=None, gt=0, le=20)
    lot_size: float | None = Field(default=None, gt=0)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    trade_id: str = Field(default_factory=lambda: f"fx-{uuid4().hex[:16]}", min_length=3, max_length=120)

    @field_validator("pair")
    @classmethod
    def normalize_pair(cls, value: str) -> str:
        return value.strip().upper().replace("/", "")

    @field_validator("timeframe")
    @classmethod
    def normalize_timeframe(cls, value: str) -> str:
        return value.strip().upper()

    @model_validator(mode="after")
    def validate_directional_prices(self) -> "ForexAutoSignal":
        if self.side == ForexTradeSide.buy:
            if self.stop_loss >= self.entry:
                raise ValueError("BUY stop_loss must be below entry.")
            if self.tp1 <= self.entry:
                raise ValueError("BUY tp1 must be above entry.")
            if self.tp2 is not None and self.tp2 <= self.tp1:
                raise ValueError("BUY tp2 must be above tp1.")
        if self.side == ForexTradeSide.sell:
            if self.stop_loss <= self.entry:
                raise ValueError("SELL stop_loss must be above entry.")
            if self.tp1 >= self.entry:
                raise ValueError("SELL tp1 must be below entry.")
            if self.tp2 is not None and self.tp2 >= self.tp1:
                raise ValueError("SELL tp2 must be below tp1.")
        return self


class MT5ConnectRequest(BaseModel):
    login: int
    password: str = Field(..., min_length=1)
    server: str = Field(..., min_length=1, max_length=120)
    terminal_path: str | None = Field(default=None, max_length=500)


class MT5AccountSnapshot(BaseModel):
    login: int | None = None
    server: str | None = None
    currency: str = "USD"
    balance: float = 0
    equity: float = 0
    margin_free: float = 0
    leverage: int | None = None
    trade_allowed: bool = False
    connected: bool = False
    name: str | None = None
    company: str | None = None


class SymbolSnapshot(BaseModel):
    symbol: str
    bid: float
    ask: float
    point: float
    digits: int
    spread_pips: float
    trade_allowed: bool = True
    volume_min: float = 0.01
    volume_max: float = 100.0
    volume_step: float = 0.01
    contract_size: float = 100_000
    pip_size: float
    pip_value_per_lot: float


class RiskLimits(BaseModel):
    minimum_lot: float = Field(default=0.01, gt=0)
    maximum_lot: float = Field(default=5.0, gt=0)
    maximum_total_lots: float = Field(default=10.0, gt=0)
    risk_per_trade_percent: float = Field(default=1.0, gt=0, le=20)
    maximum_daily_loss_percent: float = Field(default=3.0, gt=0, le=100)
    maximum_daily_profit_percent: float = Field(default=5.0, gt=0, le=100)
    maximum_trades_per_day: int = Field(default=3, ge=1)
    maximum_open_trades: int = Field(default=3, ge=1)
    maximum_spread_pips: float = Field(default=2.5, gt=0)
    minimum_confidence: float = Field(default=75.0, ge=0, le=100)
    one_trade_per_pair: bool = True
    break_even_trigger_percent: float = Field(default=0.65, ge=0.1, le=1.0)
    break_even_buffer_pips: float = Field(default=1.0, ge=0)
    partial_close_percent: float = Field(default=50.0, gt=0, le=100)
    trailing_distance_pips: float = Field(default=15.0, gt=0)


class PositionSizingResult(BaseModel):
    lot_size: float
    risk_amount: float
    stop_loss_pips: float
    pip_value_per_lot: float
    clamped: bool = False
    notes: list[str] = Field(default_factory=list)


class ValidationResult(BaseModel):
    status: SignalValidationStatus
    accepted: bool
    reasons: list[str] = Field(default_factory=list)
    sizing: PositionSizingResult | None = None
    account: MT5AccountSnapshot | None = None
    symbol: SymbolSnapshot | None = None


class OpenTradeRequest(BaseModel):
    signal: ForexAutoSignal
    dry_run: bool = False


class CloseTradeRequest(BaseModel):
    trade_id: str = Field(..., min_length=3, max_length=120)
    volume: float | None = Field(default=None, gt=0)
    reason: str = Field(default="manual_close", max_length=200)


class TradeRecord(BaseModel):
    trade_id: str
    pair: str
    side: ForexTradeSide
    timeframe: str
    entry: float
    stop_loss: float
    tp1: float
    tp2: float | None = None
    confidence: float
    risk_percent: float
    lot_size: float
    status: TradeStatus
    mt5_order_id: int | None = None
    mt5_position_id: int | None = None
    opened_at: datetime | None = None
    closed_at: datetime | None = None
    close_reason: str | None = None
    pnl: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class TradeActionResponse(BaseModel):
    accepted: bool
    message: str
    trade: TradeRecord | None = None
    validation: ValidationResult | None = None


class PerformanceSnapshot(BaseModel):
    total_trades: int
    open_trades: int
    closed_trades: int
    wins: int
    losses: int
    win_rate: float
    realized_pnl: float
    today_pnl: float
    today_trade_count: int


class MT5OrderResult(BaseModel):
    success: bool
    order_id: int | None = None
    position_id: int | None = None
    executed_price: float | None = None
    volume: float | None = None
    retcode: int | None = None
    message: str = ""


TradeEventType = Literal[
    "OPEN_REQUESTED",
    "OPENED",
    "REJECTED",
    "FAILED",
    "BREAK_EVEN_MOVED",
    "PARTIAL_TP_CLOSED",
    "TRAILING_STOP_MOVED",
    "CLOSED",
]


class TradeEvent(BaseModel):
    trade_id: str
    event_type: TradeEventType
    message: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = Field(default_factory=dict)
