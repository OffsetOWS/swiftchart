from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.models.schemas import Direction


class ApiError(BaseModel):
    detail: str


class SignalSummary(BaseModel):
    id: int
    symbol: str
    timeframe: str
    exchange: str
    direction: str
    setup_score: float | None = None
    setup_grade: str | None = None
    entry_zone: tuple[float, float]
    stop_loss: float
    take_profit_1: float
    take_profit_2: float
    risk_reward: float
    confidence: float
    status: str
    result: str
    reason: str
    invalidation: str
    created_at: datetime


class PerformanceResponse(BaseModel):
    total_signals: int
    open_signals: int
    wins: int
    losses: int
    win_rate: float
    average_r_multiple: float


class UserProfileResponse(BaseModel):
    id: str
    email: str | None = None
    created: bool = False


class TakeTradeRequest(BaseModel):
    signal_id: str = Field(..., min_length=8, max_length=240)
    symbol: str = Field(..., min_length=2, max_length=30)
    timeframe: str = Field(default="4h", max_length=12)
    exchange: str = Field(default="hyperliquid", max_length=32)
    direction: Direction
    entry_price: float = Field(..., gt=0)
    stop_loss: float = Field(..., gt=0)
    take_profit_1: float = Field(..., gt=0)
    take_profit_2: float = Field(..., gt=0)
    risk_reward: float | None = Field(default=None, ge=0)
    setup_score: float | None = Field(default=None, ge=0, le=100)
    confidence: float | None = Field(default=None, ge=0, le=100)
    market_bias: str | None = Field(default=None, max_length=240)
    market_regime: str | None = Field(default=None, max_length=160)
    liquidity_status: str | None = Field(default=None, max_length=80)
    signal_timestamp: datetime | None = None


class UserTakenTrade(BaseModel):
    id: int
    user_id: str
    signal_id: str
    symbol: str
    timeframe: str
    exchange: str
    direction: str
    entry_price: float
    stop_loss: float
    take_profit_1: float
    take_profit_2: float
    risk_reward: float | None = None
    setup_score: float | None = None
    confidence: float | None = None
    market_bias: str | None = None
    market_regime: str | None = None
    liquidity_status: str | None = None
    signal_timestamp: datetime | None = None
    status: Literal["taken", "open", "closed", "tp_hit", "sl_hit", "cancelled"] = "taken"
    result: Literal["open", "win", "loss", "closed", "cancelled"] = "open"
    pnl: float | None = None
    created_at: datetime
    taken_at: datetime
    already_taken: bool = False


class TradeUpdateRequest(BaseModel):
    status: Literal["taken", "open", "closed", "tp_hit", "sl_hit", "cancelled"] | None = None
    result: Literal["open", "win", "loss", "closed", "cancelled"] | None = None
    pnl: float | None = None


class WebhookPayload(BaseModel):
    event: str = Field(..., min_length=1, max_length=120)
    source: str | None = Field(default=None, max_length=80)
    payload: dict = Field(default_factory=dict)


class InternalScanRequest(BaseModel):
    exchange: str = "hyperliquid"
    timeframe: str = "4h"
    force: bool = True


class InternalExecuteRequest(BaseModel):
    signal_id: str = Field(..., min_length=8, max_length=240)
    symbol: str = Field(..., min_length=2, max_length=30)
    direction: Literal["BUY", "SELL"]
    entry: float = Field(..., gt=0)
    confidence: float = Field(..., ge=0, le=100)
    timeframe: str = Field(default="4h", max_length=12)
