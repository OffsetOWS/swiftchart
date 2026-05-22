from __future__ import annotations

import hashlib
import hmac
import time

from fastapi import Header, HTTPException, Request

from app.config import get_settings
from app.utils.database import claim_api_nonce


def _signature(secret: str, timestamp: str, nonce: str, body: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), f"{timestamp}.{nonce}.".encode("utf-8") + body, hashlib.sha256).hexdigest()


async def require_signed_webhook(
    request: Request,
    x_swiftchart_timestamp: str = Header(default=""),
    x_swiftchart_nonce: str = Header(default=""),
    x_swiftchart_signature: str = Header(default=""),
) -> None:
    settings = get_settings()
    secret = settings.webhook_signing_secret
    if not secret or len(secret) < 32:
        raise HTTPException(status_code=503, detail="Webhook signing is not configured.")
    if not x_swiftchart_timestamp or not x_swiftchart_nonce or not x_swiftchart_signature:
        raise HTTPException(status_code=401, detail="Missing webhook signature headers.")
    try:
        timestamp = int(x_swiftchart_timestamp)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Invalid webhook timestamp.") from exc
    if abs(time.time() - timestamp) > settings.webhook_clock_skew_seconds:
        raise HTTPException(status_code=401, detail="Webhook signature timestamp is stale.")
    expected = _signature(secret, x_swiftchart_timestamp, x_swiftchart_nonce, await request.body())
    if not hmac.compare_digest(expected, x_swiftchart_signature):
        raise HTTPException(status_code=401, detail="Invalid webhook signature.")
    if not claim_api_nonce(x_swiftchart_nonce, timestamp, settings.webhook_nonce_ttl_seconds):
        raise HTTPException(status_code=401, detail="Replay detected.")


def require_internal_secret(x_swiftchart_internal_secret: str | None = Header(default=None)) -> None:
    secret = get_settings().internal_api_secret
    if not secret or len(secret) < 32:
        raise HTTPException(status_code=503, detail="Internal API secret is not configured.")
    if not x_swiftchart_internal_secret or not hmac.compare_digest(secret, x_swiftchart_internal_secret):
        raise HTTPException(status_code=401, detail="Invalid internal API secret.")
