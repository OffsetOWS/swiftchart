from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query

from app.ea.auth import require_ea_key
from app.ea.models import EAHeartbeatRequest, EATradeUpdateRequest
from app.ea.service import EAExecutionService

router = APIRouter()
logger = logging.getLogger("swiftchart.ea")


def ea_service() -> EAExecutionService:
    return EAExecutionService()


@router.get("/ea/pending-signals")
async def ea_pending_signals(
    limit: int = Query(default=20, ge=1, le=100),
    api_key_hash: str = Depends(require_ea_key),
    service: EAExecutionService = Depends(ea_service),
):
    return service.pending_signals(limit=limit)


@router.post("/ea/trade-update")
async def ea_trade_update(
    update: EATradeUpdateRequest,
    api_key_hash: str = Depends(require_ea_key),
    service: EAExecutionService = Depends(ea_service),
):
    return service.trade_update(update, api_key_hash=api_key_hash)


@router.post("/ea/heartbeat")
async def ea_heartbeat(
    heartbeat: EAHeartbeatRequest,
    api_key_hash: str = Depends(require_ea_key),
    service: EAExecutionService = Depends(ea_service),
):
    return service.heartbeat(heartbeat, api_key_hash=api_key_hash)


@router.get("/ea/config")
async def ea_config(
    api_key_hash: str = Depends(require_ea_key),
    service: EAExecutionService = Depends(ea_service),
):
    logger.info("EA request method=GET path=/api/ea/config auth=accepted key_hash_prefix=%s", api_key_hash[:8])
    response = service.config()
    logger.info("EA response status=200 path=/api/ea/config body=%s", response.model_dump(mode="json"))
    return response
