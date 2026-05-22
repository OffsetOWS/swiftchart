from __future__ import annotations

import time
from collections import defaultdict, deque
from collections.abc import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import get_settings


class InMemoryRateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, window_seconds: int = 60):
        super().__init__(app)
        self.window_seconds = window_seconds
        self.requests: dict[str, deque[float]] = defaultdict(deque)

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if not request.url.path.startswith("/api"):
            return await call_next(request)

        settings = get_settings()
        limit = settings.api_rate_limit_per_minute
        if request.url.path in {"/api/analyze", "/api/top-ideas", "/api/candles"}:
            limit = settings.scanner_rate_limit_per_minute

        key = f"{self._client_ip(request)}:{request.url.path}"
        now = time.monotonic()
        bucket = self.requests[key]
        while bucket and now - bucket[0] > self.window_seconds:
            bucket.popleft()
        if len(bucket) >= limit:
            return Response("Rate limit exceeded.", status_code=429, headers={"Retry-After": str(self.window_seconds)})
        bucket.append(now)
        return await call_next(request)

    @staticmethod
    def _client_ip(request: Request) -> str:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",", 1)[0].strip()
        return request.client.host if request.client else "unknown"
