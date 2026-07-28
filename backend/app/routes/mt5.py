from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.ea import storage as ea_storage
from app.ea.service import EAExecutionService
from app.mt5.bridge import MT5BridgeError, get_mt5_bridge
from app.mt5.models import CloseTradeRequest, ForexAutoSignal, MT5ConnectRequest, OpenTradeRequest
from app.mt5.service import ForexExecutionService

router = APIRouter()


def execution_service() -> ForexExecutionService:
    return ForexExecutionService(get_mt5_bridge())


def ea_execution_service() -> EAExecutionService:
    return EAExecutionService()


@router.post("/forex/signal")
async def forex_signal(signal: ForexAutoSignal, service: EAExecutionService = Depends(ea_execution_service)):
    return service.receive_signal(signal)


@router.post("/legacy/mt5/connect")
async def mt5_connect(request: MT5ConnectRequest, service: ForexExecutionService = Depends(execution_service)):
    try:
        return service.connect(request)
    except MT5BridgeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/trade/open")
async def trade_open(request: OpenTradeRequest, service: EAExecutionService = Depends(ea_execution_service)):
    return service.receive_signal(request.signal, dry_run=request.dry_run)


@router.post("/legacy/trade/open")
async def legacy_trade_open(request: OpenTradeRequest, service: ForexExecutionService = Depends(execution_service)):
    return service.open_trade(request.signal, dry_run=request.dry_run)


@router.post("/legacy/trade/close")
async def trade_close(request: CloseTradeRequest, service: ForexExecutionService = Depends(execution_service)):
    try:
        return service.close_trade(request)
    except MT5BridgeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/trades")
async def trades(status: str | None = Query(default=None), limit: int = Query(default=100, ge=1, le=500)):
    return {"trades": ea_storage.list_signals(status=status, limit=limit)}


@router.get("/performance")
async def performance():
    counts = ea_storage.execution_counts()
    total = sum(counts.values())
    terminal_states = {"closed", "failed", "rejected"}
    open_count = total - sum(count for state, count in counts.items() if state in terminal_states)
    return {"total_trades": total, "open_trades": open_count, "status_counts": counts}


@router.get("/legacy/account")
async def account(service: ForexExecutionService = Depends(execution_service)):
    try:
        return service.account()
    except MT5BridgeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
