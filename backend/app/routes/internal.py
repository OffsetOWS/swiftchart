from __future__ import annotations

from fastapi import APIRouter, Depends

from app.config import get_settings
from app.models.api import InternalExecuteRequest, InternalScanRequest
from app.services.scanner import run_scan
from app.services.unified_api import record_execution_log
from app.utils.signing import require_internal_secret

router = APIRouter()


@router.post("/internal/scan", dependencies=[Depends(require_internal_secret)])
async def internal_scan(payload: InternalScanRequest):
    return await run_scan(exchange=payload.exchange, timeframe=payload.timeframe, force=payload.force)


@router.post("/internal/execute", dependencies=[Depends(require_internal_secret)])
async def internal_execute(payload: InternalExecuteRequest):
    settings = get_settings()
    if not settings.execution_autotrade_enabled:
        return record_execution_log("rejected", payload.model_dump(), "Execution is disabled.")
    return record_execution_log("queued", payload.model_dump(), "Execution request accepted for internal processing.")


@router.get("/internal/health", dependencies=[Depends(require_internal_secret)])
async def internal_health():
    settings = get_settings()
    return {
        "status": "ok",
        "environment": settings.environment,
        "execution_autotrade_enabled": settings.execution_autotrade_enabled,
        "live_trading_enabled": settings.live_trading_enabled,
    }
