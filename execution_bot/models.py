from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class SignalSide(str, Enum):
    buy = "BUY"
    sell = "SELL"


class TradeMode(str, Enum):
    paper = "paper"
    live = "live"


class BotStatus(str, Enum):
    active = "active"
    paused = "paused"
    killed = "killed"


class SignalIn(BaseModel):
    pair: str = Field(..., min_length=2, max_length=30)
    side: SignalSide
    entry: float = Field(..., gt=0)
    confidence: float = Field(..., ge=0, le=100)
    timeframe: str = Field(..., min_length=1, max_length=12)
    reason: str = Field(default="SwiftChart signal", max_length=1000)
    signal_id: str | None = Field(default=None, max_length=120)
    exchange: str = Field(default="hyperliquid", max_length=32)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("pair")
    @classmethod
    def normalize_pair(cls, value: str) -> str:
        return value.strip().upper().replace("/", "")

    @field_validator("timeframe")
    @classmethod
    def normalize_timeframe(cls, value: str) -> str:
        return value.strip().lower()


class Candle(BaseModel):
    timestamp: datetime | None = None
    open: float
    high: float
    low: float
    close: float
    volume: float = 0


class MarketSnapshot(BaseModel):
    candles: list[Candle]
    bid: float | None = None
    ask: float | None = None
    mark_price: float | None = None
    perp_volume_24h: float | None = None


class RiskSettingsSnapshot(BaseModel):
    starting_balance: float
    target_balance: float
    base_risk_percent: float
    max_risk_percent: float
    max_daily_loss_percent: float
    max_weekly_loss_percent: float
    max_open_trades: int
    max_leverage: float
    min_confidence_to_trade: float


class ExecutionPlan(BaseModel):
    signal: SignalIn
    symbol: str
    side: SignalSide
    entry: float
    stop_loss: float
    stop_distance: float
    atr: float
    atr_percent: float
    structure_level: float
    risk_percent: float
    risk_amount: float
    position_size: float
    notional_value: float
    leverage: float
    take_profits: list[dict[str, float]]
    market_condition: str
    mode: TradeMode
    with_trend: bool | None = None
    notes: list[str] = Field(default_factory=list)


class SignalDecision(BaseModel):
    accepted: bool
    reason: str
    signal: SignalIn
    plan: ExecutionPlan | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
