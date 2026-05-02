from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


Direction = Literal["Long", "Short"]
MarketCondition = Literal[
    "Trending up",
    "Trending down",
    "Range-bound",
    "Breakout",
    "Breakdown",
    "No-trade zone",
]


class Candle(BaseModel):
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


class Zone(BaseModel):
    type: Literal["support", "resistance"]
    lower: float
    upper: float
    strength: float
    touches: int


class LiquiditySweep(BaseModel):
    direction: Literal["bullish", "bearish"]
    swept_level: float
    candle_time: datetime
    reclaim_price: float
    strength: float


class RiskSettings(BaseModel):
    account_size: float = Field(default=10_000, gt=0)
    risk_per_trade_pct: float = Field(default=1.0, gt=0, le=20)
    max_open_trades: int = Field(default=3, ge=1)
    min_rr: float = Field(default=2.0, gt=0)
    preferred_timeframe: str = "4h"


class TradeIdea(BaseModel):
    symbol: str
    timeframe: str
    exchange: str
    direction: Direction
    entry_zone: tuple[float, float]
    stop_loss: float
    take_profit_1: float
    take_profit_2: float
    risk_reward_ratio: float
    reason: str
    confidence_score: float
    invalid_condition: str
    rank_score: float = 0
    position_size_units: float | None = None
    risk_amount: float | None = None


class AnalysisResponse(BaseModel):
    symbol: str
    timeframe: str
    exchange: str
    current_price: float
    market_condition: MarketCondition
    support_zones: list[Zone]
    resistance_zones: list[Zone]
    liquidity_sweeps: list[LiquiditySweep]
    trade_ideas: list[TradeIdea]
    warning: str | None = None


class Market(BaseModel):
    symbol: str
    base_asset: str
    quote_asset: str
    exchange: str


class PaperTradeCreate(BaseModel):
    symbol: str
    timeframe: str
    exchange: str = "binance"
    direction: Direction
    entry_price: float
    stop_loss: float
    take_profit_1: float
    take_profit_2: float
    size: float
    notes: str | None = None


class PaperTrade(PaperTradeCreate):
    id: int
    status: Literal["open", "closed"] = "open"
    created_at: datetime
