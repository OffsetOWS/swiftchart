from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import os
import secrets
import time

import httpx
from fastapi import APIRouter, Header, HTTPException, Query, status

from app.config import get_settings
from app.forex.models import (
    ACTIVE_FOREX_STATUSES,
    TERMINAL_FOREX_STATUSES,
    ForexOverview,
    ForexScannerDiagnostics,
    ForexSignalList,
    ForexSignalPlan,
    ForexScanRunResult,
    TakeTradePreparation,
    TakeTradeRequest,
)
from app.forex.config import normalize_forex_timeframe
from app.forex.news import forex_news_risk
from app.forex.scanner import forex_pair_infos, scan_forex
from app.forex.sessions import forex_session_state
from app.forex.storage import (
    get_scanner_diagnostics,
    get_signal,
    list_signals,
    save_trade_preparation,
)

router = APIRouter()
MANUAL_SCAN_COOLDOWN_SECONDS = 30
_manual_scans_active: set[str] = set()
_manual_scan_completed_at: dict[str, float] = {}


def _require_internal_secret(x_internal_api_secret: str | None) -> None:
    expected = get_settings().internal_api_secret
    if not expected or not x_internal_api_secret or not secrets.compare_digest(expected, x_internal_api_secret):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Scanner access denied.")


async def _require_authenticated(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer ") or len(authorization) < 24:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required.")
    token = authorization.split(" ", 1)[1].strip()
    supabase_url = os.getenv("SUPABASE_URL") or os.getenv("VITE_SUPABASE_URL")
    anon_key = os.getenv("SUPABASE_ANON_KEY") or os.getenv("VITE_SUPABASE_ANON_KEY")
    if not supabase_url or not anon_key:
        if get_settings().environment.lower() == "production":
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Authentication service is unavailable.",
            )
        return token
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(
                f"{supabase_url.rstrip('/')}/auth/v1/user",
                headers={"Authorization": f"Bearer {token}", "apikey": anon_key},
            )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service is temporarily unavailable.",
        ) from exc
    if response.status_code != 200:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Your session has expired. Please sign in again.",
        )
    user_id = str(response.json().get("id") or "")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required.")
    return user_id


@router.get("/forex/pairs")
async def forex_pairs():
    return {"market_type": "forex", "pairs": forex_pair_infos()}


@router.get("/forex/sessions")
async def forex_sessions():
    return forex_session_state()


@router.get("/forex/overview", response_model=ForexOverview)
async def forex_overview():
    settings = get_settings()
    session = forex_session_state()
    news_risk, news_reason = forex_news_risk()
    active = list_signals(ACTIVE_FOREX_STATUSES, limit=5)
    configured = bool(settings.twelve_data_api_key)
    if not configured:
        message = "Forex data provider is not configured."
    elif not active:
        message = "No persisted Forex setup currently meets the scanner criteria."
    else:
        message = None
    return ForexOverview(
        configured=configured,
        provider="twelvedata",
        active_session=session,
        supported_pairs=forex_pair_infos(),
        news_risk_warning=news_reason if news_risk != "LOW" else "News risk: LOW.",
        top_setups=active,
        message=message,
    )


@router.get("/forex/signals", response_model=ForexSignalList)
async def forex_signals(
    status_filter: str | None = Query(default=None, alias="status"),
    timeframe: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
):
    statuses = (
        tuple(item.strip().upper() for item in status_filter.split(",") if item.strip())
        if status_filter
        else None
    )
    try:
        normalized_timeframe = normalize_forex_timeframe(timeframe) if timeframe else None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    signals = list_signals(statuses, limit, normalized_timeframe)
    return ForexSignalList(signals=signals, count=len(signals))


@router.get("/signals", response_model=ForexSignalList)
async def active_signals(
    timeframe: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
):
    """Read-only active signal feed; GET never initiates market analysis."""
    try:
        normalized_timeframe = normalize_forex_timeframe(timeframe) if timeframe else None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    signals = list_signals(ACTIVE_FOREX_STATUSES, limit, normalized_timeframe)
    return ForexSignalList(signals=signals, count=len(signals))


@router.get("/forex/signals/{signal_id}", response_model=ForexSignalPlan)
async def forex_signal_detail(signal_id: str):
    signal = get_signal(signal_id)
    if signal is None:
        raise HTTPException(status_code=404, detail="Forex signal not found.")
    return signal


@router.post("/forex/scanner/run", response_model=ForexScanRunResult)
async def forex_scanner_run(
    timeframe: str = Query(default="15M"),
    x_internal_api_secret: str | None = Header(default=None),
):
    _require_internal_secret(x_internal_api_secret)
    try:
        normalized_timeframe = normalize_forex_timeframe(timeframe)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return await scan_forex(timeframe=normalized_timeframe, trigger_source="scheduled")


@router.post("/forex/scan", response_model=ForexScanRunResult)
async def manual_forex_scan(
    timeframe: str = Query(default="15M"),
    authorization: str | None = Header(default=None),
):
    await _require_authenticated(authorization)
    try:
        normalized_timeframe = normalize_forex_timeframe(timeframe)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    requester_key = hashlib.sha256(authorization.encode("utf-8")).hexdigest()
    if requester_key in _manual_scans_active:
        raise HTTPException(status_code=409, detail="A Forex scan is already running.")
    elapsed = time.monotonic() - _manual_scan_completed_at.get(requester_key, 0)
    if elapsed < MANUAL_SCAN_COOLDOWN_SECONDS:
        retry_after = max(1, int(MANUAL_SCAN_COOLDOWN_SECONDS - elapsed))
        raise HTTPException(
            status_code=429,
            detail=f"Please wait {retry_after} seconds before scanning again.",
        )

    _manual_scans_active.add(requester_key)
    try:
        return await scan_forex(
            timeframe=normalized_timeframe,
            trigger_source="manual",
        )
    finally:
        _manual_scans_active.discard(requester_key)
        _manual_scan_completed_at[requester_key] = time.monotonic()


@router.get("/forex/diagnostics", response_model=ForexScannerDiagnostics)
async def forex_scanner_diagnostics(
    x_internal_api_secret: str | None = Header(default=None),
):
    _require_internal_secret(x_internal_api_secret)
    return ForexScannerDiagnostics(**get_scanner_diagnostics())


@router.post("/forex/signals/{signal_id}/take-trade", response_model=TakeTradePreparation)
async def take_forex_trade(
    signal_id: str,
    payload: TakeTradeRequest,
    authorization: str | None = Header(default=None),
):
    await _require_authenticated(authorization)
    signal = get_signal(signal_id)
    if signal is None:
        raise HTTPException(status_code=404, detail="Forex signal not found.")
    if signal.status in TERMINAL_FOREX_STATUSES or signal.direction == "WAIT":
        raise HTTPException(status_code=409, detail="This Forex signal is no longer eligible to take.")
    stop_distance = abs(signal.entry_price - signal.stop_loss)
    if stop_distance <= 0:
        raise HTTPException(status_code=409, detail="This signal has invalid risk levels.")
    risk_amount = payload.account_balance * payload.risk_percentage / 100
    position_size = risk_amount / stop_distance
    save_trade_preparation(
        signal,
        user_id=None,
        account_balance=payload.account_balance,
        risk_percentage=payload.risk_percentage,
        risk_amount=risk_amount,
        position_size=position_size,
        execution_method=payload.execution_method,
    )
    return TakeTradePreparation(
        signal=signal,
        account_balance=payload.account_balance,
        risk_percentage=payload.risk_percentage,
        risk_amount=round(risk_amount, 2),
        stop_distance=stop_distance,
        position_size=round(position_size, 4),
        execution_method=payload.execution_method,
        prepared_at=datetime.now(UTC),
    )
