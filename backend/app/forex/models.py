from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


ForexDirection = Literal["LONG", "SHORT", "WAIT"]
ForexTimeframe = Literal["15M", "1H", "4H", "1D"]
ForexStatus = Literal[
    "PENDING_ENTRY",
    "OPEN",
    "TP1_HIT",
    "TP1_HIT_TP2_RUNNING",
    "TP2_HIT",
    "STOPPED",
    "EXPIRED",
    "CANCELLED",
]
ACTIVE_FOREX_STATUSES = ("PENDING_ENTRY", "OPEN", "TP1_HIT_TP2_RUNNING")
DISPLAY_ACTIVE_FOREX_STATUSES = ("PENDING_ENTRY", "OPEN", "TP1_HIT_TP2_RUNNING")
TERMINAL_FOREX_STATUSES = ("TP1_HIT", "TP2_HIT", "STOPPED", "EXPIRED", "CANCELLED")

CrossMarketState = Literal[
    "STRONG_BULLISH", "BULLISH", "NEUTRAL", "BEARISH", "STRONG_BEARISH", "UNAVAILABLE"
]
OilMarketState = Literal[
    "STRONG_RALLY", "RALLY", "NEUTRAL", "SELLOFF", "STRONG_SELLOFF", "UNAVAILABLE"
]
LimitOrderType = Literal["BUY_LIMIT", "SELL_LIMIT"]
LimitOpportunityStatus = Literal[
    "WAIT_FOR_RETEST", "PENDING_LIMIT", "ACTIVE_TRADE", "TP1_HIT", "TP1_HIT_TP2_RUNNING", "TP2_HIT", "SL_HIT",
    "EXPIRED", "CANCELLED", "INVALIDATED", "MISSED_NO_RETEST",
    "TARGET_REACHED_BEFORE_ENTRY", "NEWS_CANCELLED",
]


class MarketContextComponent(BaseModel):
    instrument: Literal["DXY", "WTI"]
    source: Literal["PROVIDER_DXY", "SYNTHETIC_USD_BASKET", "PROVIDER_WTI", "UNAVAILABLE"] = "UNAVAILABLE"
    state: str
    direction: Literal["BULLISH", "BEARISH", "NEUTRAL", "UNAVAILABLE"]
    strength_score: float = Field(ge=0, le=100)
    primary_timeframe: str
    higher_timeframe_state: str | None = None
    alignment_status: Literal["STRONG_ALIGNMENT", "ALIGNED", "NEUTRAL", "CONFLICT", "STRONG_CONFLICT", "UNAVAILABLE"]
    confidence_adjustment: float
    explanation: str
    candle_timestamp: datetime | None = None
    higher_timeframe_candle_timestamp: datetime | None = None
    stale: bool = False


class ForexCrossMarketContext(BaseModel):
    pair: str
    timeframe: str
    usd_context: MarketContextComponent | None = None
    oil_context: MarketContextComponent | None = None
    total_adjustment: float = Field(ge=-10, le=10)
    evaluated_at: datetime
    source_candle_times: dict[str, datetime | None] = Field(default_factory=dict)
    stale: bool = False
    explanation: str


class FairValueGap(BaseModel):
    lower: float
    upper: float
    midpoint: float
    gap_size_pips: float
    gap_size_atr: float
    creation_candle_time: datetime
    mitigation_percentage: float = Field(default=0, ge=0, le=100)
    status: Literal["FRESH", "PARTIALLY_MITIGATED", "INVALID"] = "FRESH"


class ForexLimitOpportunity(BaseModel):
    id: str
    pair: str
    timeframe: Literal["1H", "4H", "1D"]
    strategy_family: Literal["liquidity_sweep_fvg_limit"] = "liquidity_sweep_fvg_limit"
    strategy_version: Literal["liquidity_sweep_fvg_limit_v1"] = "liquidity_sweep_fvg_limit_v1"
    experimental: bool = True
    shadow_mode: bool = True
    auto_execution_enabled: bool = False
    order_type: LimitOrderType
    opportunity_status: LimitOpportunityStatus
    direction: Literal["LONG", "SHORT"]
    entry_price: float
    entry_zone_low: float
    entry_zone_high: float
    entry_mode: str
    current_price: float
    market_session: str = "Unknown"
    distance_to_entry_pips: float
    sweep_level: float
    sweep_extreme: float
    sweep_candle_time: datetime
    displacement_candle_time: datetime
    fvg: FairValueGap
    stop_loss: float
    take_profit_1: float
    take_profit_2: float
    tp1_closes_position: bool = False
    risk_pips: float
    reward_1_pips: float
    reward_2_pips: float
    risk_reward_1: float
    risk_reward_2: float
    suggested_position_size: float | None = None
    expiry_time: datetime
    expiry_candle_count: int
    fill_time: datetime | None = None
    closed_at: datetime | None = None
    cancellation_reason: str | None = None
    invalidation_reason: str | None = None
    technical_score: float = Field(ge=0, le=100)
    final_score: float = Field(ge=0, le=100)
    context: ForexCrossMarketContext
    reasoning: list[str]
    dedupe_key: str
    created_at: datetime
    updated_at: datetime
    mae_pips: float = 0
    mfe_pips: float = 0
    pnl_r: float | None = None
    lifecycle_events: list[dict[str, Any]] = Field(default_factory=list)


class ForexLimitOpportunityList(BaseModel):
    opportunities: list[ForexLimitOpportunity]
    count: int


class ForexSessionState(BaseModel):
    active_session: str
    next_session: str
    next_session_open: datetime | None = None
    time_until_next_session_minutes: int | None = None
    is_pre_session: bool = False
    is_session_open: bool = False
    is_overlap: bool = False
    market_open: bool = True
    label: str


class ForexSignalPlan(BaseModel):
    id: str
    symbol: str
    direction: ForexDirection
    entry_type: str = "ZONE"
    entry_price: float
    entry_low: float
    entry_high: float
    stop_loss: float
    take_profit_1: float
    take_profit_2: float
    tp1_closes_position: bool = False
    risk_reward_1: float
    risk_reward_2: float
    timeframe: ForexTimeframe = "1H"
    execution_timeframe: str = "1h"
    setup_timeframe: str = "4h"
    bias_timeframe: str = "1d"
    timeframe_alignment: str
    htf_bias: str
    setup_structure: str
    entry_trigger: str
    market_session: str
    setup_score: float = Field(ge=0, le=100)
    technical_score: float | None = Field(default=None, ge=0, le=100)
    context_adjustment: float = Field(default=0, ge=-10, le=10)
    cross_market_context: ForexCrossMarketContext | None = None
    strategy_family: str
    strategy_version: str
    market_regime: str
    bias: str
    setup_reason: str
    status: ForexStatus
    created_at: datetime
    activated_at: datetime | None = None
    expires_at: datetime
    closed_at: datetime | None = None
    telegram_dispatched_at: datetime | None = None
    source_scan_id: str
    dedupe_key: str
    latest_price: float | None = None
    latest_price_at: datetime | None = None
    activated_entry_price: float | None = None
    tp1_hit_at: datetime | None = None
    tp2_hit_at: datetime | None = None
    stopped_at: datetime | None = None
    last_market_price: float | None = None
    last_price_updated_at: datetime | None = None
    is_legacy: bool = False


class ForexSignalList(BaseModel):
    market_type: Literal["forex"] = "forex"
    signals: list[ForexSignalPlan]
    count: int


class ForexScanRunResult(BaseModel):
    scan_id: str
    configured: bool
    scanned_at: datetime
    completed_at: datetime | None = None
    timeframe: ForexTimeframe = "1H"
    trigger_source: Literal["scheduled", "manual"] = "scheduled"
    result_status: Literal[
        "TRADE_FOUND",
        "WAIT_FOR_RETEST",
        "NO_TRADE",
        "MARKET_CLOSED",
        "DATA_UNAVAILABLE",
        "FAILED",
    ] = "NO_TRADE"
    pairs_scanned: int = 0
    candidates_found: int = 0
    persisted_count: int = 0
    telegram_queued: int = 0
    rejection_reasons: list[str] = Field(default_factory=list)
    created: list[ForexSignalPlan]
    reused: list[ForexSignalPlan]
    rejected: list[dict[str, str | float]]
    errors: list[str]


class ForexScannerDiagnostics(BaseModel):
    last_scheduled_scan_time: datetime | None = None
    last_successful_scan_time: datetime | None = None
    last_scan_timeframe: str | None = None
    last_trigger_source: str | None = None
    pairs_evaluated: int = 0
    candidates_found: int = 0
    rejected: int = 0
    persisted: int = 0
    telegram_queued: int = 0
    telegram_delivered: int = 0
    latest_scanner_error: str | None = None
    latest_telegram_error: str | None = None


class TakeTradeRequest(BaseModel):
    account_balance: float = Field(gt=0)
    risk_percentage: float = Field(gt=0, le=20)
    execution_method: str = Field(min_length=2, max_length=64)


class TakeTradePreparation(BaseModel):
    signal: ForexSignalPlan
    account_balance: float
    risk_percentage: float
    risk_amount: float
    stop_distance: float
    position_size: float
    execution_method: str
    prepared_at: datetime
    execution_status: Literal["PREPARED"] = "PREPARED"


class ForexPairInfo(BaseModel):
    pair: str
    pip_size: float
    sessions: list[str]
    max_spread_pips: float
    volatility_rules: dict[str, float]
    default_timeframes: dict[str, str]


class ForexOverview(BaseModel):
    market_type: Literal["forex"] = "forex"
    configured: bool
    provider: str
    active_session: ForexSessionState
    supported_pairs: list[ForexPairInfo]
    news_risk_warning: str
    top_setups: list[ForexSignalPlan] = Field(default_factory=list)
    message: str | None = None
