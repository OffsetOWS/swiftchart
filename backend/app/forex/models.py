from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


ForexDirection = Literal["LONG", "SHORT", "WAIT"]
ForexTimeframe = Literal["15M", "1H", "4H", "1D"]
ForexStatus = Literal[
    "PENDING_ENTRY",
    "OPEN",
    "TP1_HIT",
    "TP2_HIT",
    "STOPPED",
    "EXPIRED",
    "CANCELLED",
]
ACTIVE_FOREX_STATUSES = ("PENDING_ENTRY", "OPEN", "TP1_HIT")
TERMINAL_FOREX_STATUSES = ("TP2_HIT", "STOPPED", "EXPIRED", "CANCELLED")


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
    risk_reward_1: float
    risk_reward_2: float
    timeframe: ForexTimeframe = "15M"
    execution_timeframe: str = "15m"
    setup_timeframe: str = "1h"
    bias_timeframe: str = "4h"
    timeframe_alignment: str
    htf_bias: str
    setup_structure: str
    entry_trigger: str
    market_session: str
    setup_score: float = Field(ge=0, le=100)
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
    timeframe: ForexTimeframe = "15M"
    trigger_source: Literal["scheduled", "manual"] = "scheduled"
    result_status: Literal["TRADE_FOUND", "NO_TRADE", "FAILED"] = "NO_TRADE"
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
