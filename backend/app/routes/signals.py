from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.models.api import PerformanceResponse, SignalSummary
from app.services.trade_history import stats
from app.services.unified_api import get_signal, list_signals

router = APIRouter()


@router.get("/signals", response_model=list[SignalSummary])
async def signals(limit: int = Query(default=50, ge=1, le=250), symbol: str | None = Query(default=None)):
    return list_signals(limit=limit, symbol=symbol)


@router.get("/signals/{signal_id}", response_model=SignalSummary)
async def signal_detail(signal_id: int):
    signal = get_signal(signal_id)
    if signal is None:
        raise HTTPException(status_code=404, detail="Signal not found.")
    return signal


@router.get("/performance", response_model=PerformanceResponse)
async def performance():
    data = stats()
    return PerformanceResponse(
        total_signals=data["total_ideas"],
        open_signals=data["open_count"],
        wins=data["win_count"],
        losses=data["loss_count"],
        win_rate=data["win_rate"],
        average_r_multiple=data["average_r_multiple"],
    )
