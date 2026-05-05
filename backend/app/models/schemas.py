from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


Direction = Literal["Long", "Short"]
TradeHistoryStatus = Literal[
    "PENDING",
    "ENTRY_TRIGGERED",
    "TP1_HIT",
    "TP2_HIT",
    "SL_HIT",
    "EXPIRED",
    "INVALIDATED",
    "AMBIGUOUS",
]
TradeHistoryResult = Literal["WIN", "PARTIAL_WIN", "LOSS", "NO_ENTRY", "AMBIGUOUS", "OPEN"]
MarketCondition = Literal[
    "RANGE_BOUND",
    "TRENDING_UP",
    "TRENDING_DOWN",
    "BREAKOUT",
    "BREAKDOWN",
    "CHOP",
    "NO_TRADE",
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
    lower_bound: float | None = None
    upper_bound: float | None = None
    strength_score: float | None = None
    last_reaction_time: datetime | None = None
    role: Literal["SUPPORT", "RESISTANCE"] | None = None


class LiquiditySweep(BaseModel):
    direction: Literal["bullish", "bearish"]
    swept_level: float
    candle_time: datetime
    reclaim_price: float
    strength: float
    sweep_direction: Literal["bullish", "bearish"] | None = None
    confirmation_status: Literal["confirmed", "unconfirmed"] = "confirmed"
    sweep_quality_score: float | None = None


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
    market_regime: MarketCondition | None = None
    higher_timeframe_bias: Literal["HTF_BULLISH", "HTF_BEARISH", "HTF_NEUTRAL"] = "HTF_NEUTRAL"
    setup_grade: str | None = None
    setup_score: float | None = None
    entry_zone: tuple[float, float]
    stop_loss: float
    take_profit_1: float
    take_profit_2: float
    risk_reward_ratio: float
    reason: str
    confidence_score: float
    invalid_condition: str
    warning: str = "Not financial advice. Manage risk."
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
    higher_timeframe_bias: Literal["HTF_BULLISH", "HTF_BEARISH", "HTF_NEUTRAL"] = "HTF_NEUTRAL"
    no_trade_reason: str | None = None


class Market(BaseModel):
    symbol: str
    display_symbol: str | None = None
    raw_symbol: str | None = None
    base_asset: str
    quote_asset: str
    exchange: str
    dex: str | None = None
    is_hip3: bool = False


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


class TradeHistoryRecord(BaseModel):
    id: int
    symbol: str
    timeframe: str
    exchange: str
    direction: str
    market_regime: str | None = None
    higher_timeframe_bias: str | None = None
    setup_score: float | None = None
    setup_grade: str | None = None
    entry_zone_low: float
    entry_zone_high: float
    stop_loss: float
    take_profit_1: float
    take_profit_2: float
    risk_reward: float
    confidence: float
    reason: str
    invalidation: str
    created_at: datetime
    status: TradeHistoryStatus
    outcome_checked_at: datetime | None = None
    entry_triggered_at: datetime | None = None
    closed_at: datetime | None = None
    result: TradeHistoryResult
    pnl_r_multiple: float | None = None


class TradeStats(BaseModel):
    total_ideas: int
    entry_triggered_count: int
    win_count: int
    loss_count: int
    no_entry_count: int
    ambiguous_count: int
    open_count: int
    tp_hit_rate: float
    sl_hit_rate: float
    win_rate: float
    average_r_multiple: float
    best_setup_grade_performance: list[dict]
    best_timeframe_performance: list[dict]
    best_symbol_performance: list[dict]


class TradeHistoryPage(BaseModel):
    records: list[TradeHistoryRecord]
    page: int
    limit: int
    total: int
    pages: int
    sort: Literal["asc", "desc"]
