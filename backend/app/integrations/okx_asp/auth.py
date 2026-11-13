from __future__ import annotations

import hashlib
import secrets
import threading
import time
from collections import defaultdict, deque

from fastapi import Depends, Header, HTTPException, Request

from app.config import get_settings


_rate_buckets: dict[str, deque[float]] = defaultdict(deque)
_public_rate_buckets: dict[str, deque[float]] = defaultdict(deque)
_rate_lock = threading.Lock()
_RATE_WINDOW_SECONDS = 60


def _key_hash(api_key: str) -> str:
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


def require_okx_asp_key(x_swiftchart_asp_key: str | None = Header(default=None)) -> str:
    configured = get_settings().okx_asp_api_key
    if not configured:
        raise HTTPException(status_code=503, detail="OKX ASP authentication is not configured.")
    if not x_swiftchart_asp_key:
        raise HTTPException(status_code=401, detail="Missing X-SwiftChart-ASP-Key header.")
    if not secrets.compare_digest(x_swiftchart_asp_key, configured):
        raise HTTPException(status_code=401, detail="Invalid ASP API key.")
    return _key_hash(x_swiftchart_asp_key)


def enforce_okx_asp_rate_limit(
    request: Request,
    api_key_hash: str = Depends(require_okx_asp_key),
) -> str:
    limit = get_settings().okx_asp_rate_limit_per_minute
    client_host = request.client.host if request.client else "unknown"
    bucket_key = f"{api_key_hash}:{client_host}"
    now = time.monotonic()

    with _rate_lock:
        bucket = _rate_buckets[bucket_key]
        while bucket and now - bucket[0] > _RATE_WINDOW_SECONDS:
            bucket.popleft()
        if len(bucket) >= limit:
            retry_after = max(1, int(_RATE_WINDOW_SECONDS - (now - bucket[0])))
            raise HTTPException(
                status_code=429,
                detail="OKX ASP rate limit exceeded.",
                headers={"Retry-After": str(retry_after)},
            )
        bucket.append(now)
    return api_key_hash


def enforce_okx_public_rate_limit(request: Request) -> None:
    if getattr(request.state, "okx_public_rate_limit_checked", False):
        return
    limit = get_settings().okx_asp_public_rate_limit_per_minute
    client_host = request.client.host if request.client else "unknown"
    now = time.monotonic()

    with _rate_lock:
        bucket = _public_rate_buckets[client_host]
        while bucket and now - bucket[0] > _RATE_WINDOW_SECONDS:
            bucket.popleft()
        if len(bucket) >= limit:
            retry_after = max(1, int(_RATE_WINDOW_SECONDS - (now - bucket[0])))
            raise HTTPException(
                status_code=429,
                detail="OKX public gateway rate limit exceeded.",
                headers={"Retry-After": str(retry_after)},
            )
        bucket.append(now)
    request.state.okx_public_rate_limit_checked = True


def reset_okx_asp_rate_limit_for_tests() -> None:
    with _rate_lock:
        _rate_buckets.clear()
        _public_rate_buckets.clear()
