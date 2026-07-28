from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


ForexDirection = Literal["LONG", "SHORT", "WAIT"]
ForexNewsRisk = Literal["LOW", "MEDIUM", "HIGH"]
ForexSpreadStatus = Literal["SAFE", "WIDE", "UNKNOWN"]


class ForexCandle(BaseModel):
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0


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


class ForexSignal(BaseModel):
    marketType: Literal["forex"] = "forex"
    pair: str
    direction: ForexDirection
    score: float = Field(ge=0, le=100)
    grade: str
    session: str
    pre_session_bias: str
    entry: float | None = None
    stopLoss: float | None = None
    tp1: float | None = None
    tp2: float | None = None
    rr: float | None = None
    spreadStatus: ForexSpreadStatus = "UNKNOWN"
    newsRisk: ForexNewsRisk = "LOW"
    reason: str
    lastUpdated: datetime
    status: Literal["active", "wait"] = "wait"
    timeframe: str = "15m"
    provider: str = "twelvedata"


class ForexPairInfo(BaseModel):
    pair: str
    pipSize: float
    sessions: list[str]
    maxSpreadPips: float
    volatilityRules: dict[str, float]
    defaultTimeframes: dict[str, str]


class ForexScanResponse(BaseModel):
    marketType: Literal["forex"] = "forex"
    configured: bool
    provider: str
    activeSession: ForexSessionState
    signals: list[ForexSignal]
    topSetups: list[ForexSignal]
    supportedPairs: list[ForexPairInfo]
    newsRisk: ForexNewsRisk
    message: str | None = None
    scannedAt: datetime
    errors: list[str] = Field(default_factory=list)


class ForexOverview(BaseModel):
    marketType: Literal["forex"] = "forex"
    configured: bool
    provider: str
    activeSession: ForexSessionState
    nextSessionOpen: datetime | None = None
    preSessionScanStatus: str
    supportedPairs: list[ForexPairInfo]
    newsRiskWarning: str
    topSetups: list[ForexSignal] = Field(default_factory=list)
    message: str | None = None
