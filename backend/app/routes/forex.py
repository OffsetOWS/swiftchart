from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from app.config import get_settings
from app.forex.config import SUPPORTED_FOREX_PAIRS
from app.forex.models import ForexOverview
from app.forex.news import forex_news_risk
from app.forex.scanner import forex_pair_infos, scan_forex
from app.forex.sessions import forex_session_state

router = APIRouter()

_last_scan = None


SUPPORTED_SCAN_TIMEFRAMES = {"15m", "30m", "1h", "2h", "4h", "6h", "1d"}


class ForexScanRequest(BaseModel):
    pair: str | None = Field(default=None, max_length=20)
    timeframe: str = Field(default="15m", max_length=16)

    @field_validator("pair")
    @classmethod
    def normalize_pair(cls, value: str | None) -> str | None:
        return value.strip().upper().replace("/", "") if value else value

    @field_validator("timeframe")
    @classmethod
    def normalize_timeframe(cls, value: str) -> str:
        return value.strip().lower()


@router.get("/forex/pairs")
async def forex_pairs():
    return {"marketType": "forex", "pairs": forex_pair_infos()}


@router.get("/forex/sessions")
async def forex_sessions():
    return forex_session_state()


@router.get("/forex/overview", response_model=ForexOverview)
async def forex_overview():
    settings = get_settings()
    session = forex_session_state()
    news_risk, news_reason = forex_news_risk()
    configured = bool(settings.twelve_data_api_key)
    top = getattr(_last_scan, "topSetups", []) if _last_scan else []
    if not configured:
        message = "Forex data provider is not configured."
    elif not session.market_open:
        message = "Forex market is currently closed. Next session opens soon."
    elif not top:
        message = "No clean forex setups right now."
    else:
        message = None
    return ForexOverview(
        configured=configured,
        provider="twelvedata",
        activeSession=session,
        nextSessionOpen=session.next_session_open,
        preSessionScanStatus="Preparing bias only" if session.is_pre_session else "Ready",
        supportedPairs=forex_pair_infos(),
        newsRiskWarning=news_reason if news_risk != "LOW" else "News risk placeholder: LOW.",
        topSetups=top,
        message=message,
    )


@router.get("/forex/signals")
async def forex_signals():
    global _last_scan
    if _last_scan is None:
        _last_scan = await scan_forex(save=False)
    return _last_scan


@router.post("/forex/scan")
async def forex_scan(request: ForexScanRequest | None = None):
    global _last_scan
    if request:
        if request.pair and request.pair not in SUPPORTED_FOREX_PAIRS:
            raise HTTPException(status_code=400, detail="Unsupported Forex pair.")
        if request.timeframe not in SUPPORTED_SCAN_TIMEFRAMES:
            raise HTTPException(status_code=400, detail="Unsupported Forex timeframe.")
    _last_scan = await scan_forex(save=True)
    return _last_scan
