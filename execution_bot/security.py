from __future__ import annotations

import hashlib
import hmac
import logging
import re
import time

from fastapi import Header, HTTPException, Request

from execution_bot.config import get_execution_settings
from execution_bot.storage import claim_webhook_nonce


SECRET_PATTERNS = [
    re.compile(r"(bot)[0-9]{6,}:[A-Za-z0-9_-]{20,}", re.IGNORECASE),
    re.compile(r"0x[a-fA-F0-9]{64}"),
    re.compile(r"(?i)(token|secret|private[_-]?key|api[_-]?key|password)(=)[\"']?([^,'\"\s}]+)[\"']?"),
    re.compile(r"(?i)(token|secret|private[_-]?key|api[_-]?key|password)(['\"\s:=]+)([^,'\"\s}]+)"),
    re.compile(r"(?i)(authorization|cookie|set-cookie)(['\"\s:=]+)([^,'\"\s}]+)"),
    re.compile(r"(?i)([?&](?:account_size|risk_per_trade_pct|min_rr|max_open_trades|accountSize|riskPerTradePct)=)([^&\s]+)"),
]


def redact_sensitive(value: object) -> str:
    text = str(value)
    for pattern in SECRET_PATTERNS:
        if pattern.groups >= 3:
            text = pattern.sub(r"\1\2[REDACTED]", text)
        elif pattern.groups == 2:
            text = pattern.sub(r"\1[REDACTED]", text)
        else:
            text = pattern.sub("[REDACTED]", text)
    return text


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if record.name == "uvicorn.access" and isinstance(record.args, tuple):
            record.msg = redact_sensitive(record.msg)
            record.args = tuple(redact_sensitive(arg) if isinstance(arg, str) else arg for arg in record.args)
            return True
        record.msg = redact_sensitive(record.getMessage())
        record.args = ()
        return True


def install_secure_logging() -> None:
    root = logging.getLogger()
    if not any(isinstance(filter_, RedactingFilter) for filter_ in root.filters):
        root.addFilter(RedactingFilter())
    for handler in root.handlers:
        if not any(isinstance(filter_, RedactingFilter) for filter_ in handler.filters):
            handler.addFilter(RedactingFilter())
    for logger_name in ("uvicorn.access", "uvicorn.error", "httpx"):
        logger = logging.getLogger(logger_name)
        if not any(isinstance(filter_, RedactingFilter) for filter_ in logger.filters):
            logger.addFilter(RedactingFilter())


def signature_for(secret: str, timestamp: str, nonce: str, body: bytes) -> str:
    payload = f"{timestamp}.{nonce}.".encode("utf-8") + body
    return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


async def require_signed_request(
    request: Request,
    x_swiftchart_timestamp: str = Header(default=""),
    x_swiftchart_nonce: str = Header(default=""),
    x_swiftchart_signature: str = Header(default=""),
) -> None:
    settings = get_execution_settings()
    secret = settings.execution_webhook_secret
    if not secret or len(secret) < 32:
        raise HTTPException(status_code=500, detail="Execution signing secret is not configured.")
    if not x_swiftchart_timestamp or not x_swiftchart_nonce or not x_swiftchart_signature:
        raise HTTPException(status_code=401, detail="Missing execution signature headers.")
    try:
        timestamp = int(x_swiftchart_timestamp)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Invalid execution signature timestamp.") from exc
    if abs(time.time() - timestamp) > settings.execution_auth_clock_skew_seconds:
        raise HTTPException(status_code=401, detail="Execution signature timestamp is stale.")
    body = await request.body()
    expected = signature_for(secret, x_swiftchart_timestamp, x_swiftchart_nonce, body)
    if not hmac.compare_digest(expected, x_swiftchart_signature):
        raise HTTPException(status_code=401, detail="Invalid execution signature.")
    if not claim_webhook_nonce(x_swiftchart_nonce, timestamp, settings.execution_nonce_ttl_seconds):
        raise HTTPException(status_code=401, detail="Replay detected.")
