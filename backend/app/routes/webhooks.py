from __future__ import annotations

from fastapi import APIRouter, Depends

from app.models.api import WebhookPayload
from app.services.unified_api import record_webhook
from app.utils.signing import require_signed_webhook

router = APIRouter()


@router.post("/webhooks/discord", dependencies=[Depends(require_signed_webhook)])
async def discord_webhook(payload: WebhookPayload):
    return record_webhook("discord", payload.event, payload.payload)


@router.post("/webhooks/telegram", dependencies=[Depends(require_signed_webhook)])
async def telegram_webhook(payload: WebhookPayload):
    return record_webhook("telegram", payload.event, payload.payload)


@router.post("/webhooks/signal", dependencies=[Depends(require_signed_webhook)])
async def signal_webhook(payload: WebhookPayload):
    return record_webhook(payload.source or "signal", payload.event, payload.payload)
