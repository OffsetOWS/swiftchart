from __future__ import annotations

import hashlib
import logging
import secrets

from fastapi import Header, HTTPException

from app.config import get_settings

logger = logging.getLogger("swiftchart.ea")


def hash_ea_key(api_key: str) -> str:
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


def require_ea_key(x_swiftchart_ea_key: str | None = Header(default=None)) -> str:
    configured = get_settings().ea_api_key
    if not configured:
        logger.warning("EA auth failed status=503 reason=api_key_not_configured")
        raise HTTPException(status_code=503, detail="EA API key is not configured.")
    if not x_swiftchart_ea_key:
        logger.warning("EA auth failed status=401 reason=missing_header")
        raise HTTPException(status_code=401, detail="Missing X-SwiftChart-EA-Key header.")
    if not secrets.compare_digest(x_swiftchart_ea_key, configured):
        logger.warning(
            "EA auth failed status=403 reason=invalid_key key_hash_prefix=%s",
            hash_ea_key(x_swiftchart_ea_key)[:8],
        )
        raise HTTPException(status_code=403, detail="Invalid EA API key.")
    logger.info("EA auth accepted key_hash_prefix=%s", hash_ea_key(x_swiftchart_ea_key)[:8])
    return hash_ea_key(x_swiftchart_ea_key)
