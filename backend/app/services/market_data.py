from __future__ import annotations

import asyncio
import logging
from time import monotonic
from typing import Any

import pandas as pd

from app.exchanges.factory import get_exchange

logger = logging.getLogger(__name__)

_candle_cache: dict[tuple[str, str, str, int], tuple[float, pd.DataFrame]] = {}
_market_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_locks: dict[str, asyncio.Lock] = {}


def _lock(name: str) -> asyncio.Lock:
    if name not in _locks:
        _locks[name] = asyncio.Lock()
    return _locks[name]


def candle_ttl(timeframe: str) -> int:
    normalized = timeframe.lower()
    if normalized in {"30m", "1h"}:
        return 45
    if normalized in {"2h", "4h"}:
        return 90
    return 240


async def _with_retries(coro_factory, *, attempts: int = 3, base_delay: float = 0.35):
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            return await coro_factory()
        except Exception as exc:
            last_error = exc
            if attempt < attempts - 1:
                await asyncio.sleep(base_delay * (attempt + 1))
    raise last_error  # type: ignore[misc]


async def get_markets_cached(exchange: str) -> list[dict]:
    normalized = exchange.lower()
    now = monotonic()
    cached = _market_cache.get(normalized)
    if cached and now - cached[0] < 1200:
        return cached[1]

    async with _lock(f"markets:{normalized}"):
        cached = _market_cache.get(normalized)
        now = monotonic()
        if cached and now - cached[0] < 1200:
            return cached[1]

        markets = await _with_retries(lambda: get_exchange(normalized).get_markets())
        normalized_markets = []
        for market in markets:
            normalized_markets.append(
                {
                    **market,
                    "symbol": str(market.get("symbol", "")).upper(),
                    "exchange": normalized,
                    "active": market.get("active", True),
                    "volume": market.get("volume"),
                }
            )
        _market_cache[normalized] = (monotonic(), normalized_markets)
        logger.info("Cached %s markets for %s", len(normalized_markets), normalized)
        return normalized_markets


async def get_candles_cached(exchange: str, symbol: str, timeframe: str, limit: int = 300) -> pd.DataFrame:
    normalized_exchange = exchange.lower()
    normalized_symbol = symbol.upper()
    normalized_timeframe = timeframe.lower()
    key = (normalized_exchange, normalized_symbol, normalized_timeframe, int(limit))
    now = monotonic()
    cached = _candle_cache.get(key)
    if cached and now - cached[0] < candle_ttl(normalized_timeframe):
        return cached[1].copy()

    async with _lock(f"candles:{':'.join(map(str, key))}"):
        cached = _candle_cache.get(key)
        now = monotonic()
        if cached and now - cached[0] < candle_ttl(normalized_timeframe):
            return cached[1].copy()

        df = await _with_retries(lambda: get_exchange(normalized_exchange).get_candles(normalized_symbol, normalized_timeframe, limit))
        _candle_cache[key] = (monotonic(), df.copy())
        return df
