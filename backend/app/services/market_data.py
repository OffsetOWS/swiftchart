from __future__ import annotations

import asyncio
from contextvars import ContextVar
from contextlib import contextmanager
import heapq
import itertools
import fcntl
import logging
import time
from time import monotonic
from typing import Any

import pandas as pd

from app.exchanges.factory import get_exchange
from app.config import get_settings

logger = logging.getLogger(__name__)

_candle_cache: dict[tuple[str, str, str, int], tuple[float, pd.DataFrame]] = {}
_market_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_locks: dict[str, asyncio.Lock] = {}
_candle_semaphores: dict[str, CandleSlots] = {}
_interactive = ContextVar('interactive_candles', default=False)


@contextmanager
def interactive_candles():
    token = _interactive.set(True)
    try:
        yield
    finally:
        _interactive.reset(token)


class CandleSlots:
    """Preserve provider concurrency/pacing; interactive reads avoid scan queues.

    After three interactive admissions, an already waiting scanner gets a turn.
    Cancellation never consumes a slot.
    """
    def __init__(self, limit):
        self.limit, self.used, self.streak = limit, 0, 0
        self.waiters = []
        self.sequence = itertools.count()

    async def __aenter__(self):
        future = asyncio.get_running_loop().create_future()
        heapq.heappush(self.waiters, (0 if _interactive.get() else 1, next(self.sequence), future))
        self._admit()
        try:
            await future
        except BaseException:
            if future.done() and not future.cancelled():
                self.used -= 1
            else:
                future.cancel()
            self._admit()
            raise
        return self

    async def __aexit__(self, *_):
        self.used -= 1
        self._admit()

    def _admit(self):
        self.waiters = [row for row in self.waiters if not row[2].cancelled()]
        heapq.heapify(self.waiters)
        while self.used < self.limit and self.waiters:
            background = next((row for row in sorted(self.waiters) if row[0] == 1), None)
            if self.streak >= 3 and background:
                self.waiters.remove(background)
                heapq.heapify(self.waiters)
                priority, _, future = background
            else:
                priority, _, future = heapq.heappop(self.waiters)
            self.streak = self.streak + 1 if priority == 0 else 0
            self.used += 1
            future.set_result(None)


def _lock(name: str) -> asyncio.Lock:
    if name not in _locks:
        _locks[name] = asyncio.Lock()
    return _locks[name]


def _candle_semaphore(exchange: str) -> CandleSlots:
    normalized = exchange.lower()
    if normalized not in _candle_semaphores:
        limit = get_settings().hyperliquid_candle_concurrency if normalized == "hyperliquid" else 8
        _candle_semaphores[normalized] = CandleSlots(limit)
    return _candle_semaphores[normalized]


async def _pace_candle_request(exchange: str) -> None:
    normalized = exchange.lower()
    if normalized != "hyperliquid":
        return
    delay = get_settings().hyperliquid_candle_request_delay_seconds
    if delay <= 0:
        return
    lock_path = "/tmp/swiftchart_hyperliquid_candle_pace.lock"
    with open(lock_path, "a+", encoding="utf-8") as lock_file:
        # Another process may hold this lock while pacing. A blocking flock
        # here freezes the API event loop, including /health.
        while True:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                await asyncio.sleep(0.01)
        lock_file.seek(0)
        raw = lock_file.read().strip()
        try:
            last_request_at = float(raw) if raw else 0.0
        except ValueError:
            last_request_at = 0.0
        now = time.time()
        elapsed = now - last_request_at
        if elapsed < delay:
            await asyncio.sleep(delay - elapsed)
        lock_file.seek(0)
        lock_file.truncate()
        lock_file.write(str(time.time()))
        lock_file.flush()
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


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


async def _fetch_candles_with_retries(exchange: str, symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
    settings = get_settings()
    attempts = settings.hyperliquid_candle_fetch_attempts if exchange == "hyperliquid" else 3
    base_delay = settings.hyperliquid_candle_fetch_backoff_seconds if exchange == "hyperliquid" else 0.35
    last_error: Exception | None = None
    provider_symbol = symbol
    if exchange == 'hyperliquid':
        # Normalised catalogue symbols are uppercase; provider identifiers such
        # as kPEPE are case-sensitive. Resolve the original identifier once.
        try:
            catalogue = await get_markets_cached(exchange)
            provider_symbol = next((row.get('exchange_symbol', row['symbol']) for row in catalogue
                                    if row['symbol'].upper() == symbol.upper()), symbol)
        except Exception:
            pass
    for attempt in range(1, attempts + 1):
        try:
            async with _candle_semaphore(exchange):
                await _pace_candle_request(exchange)
                from datetime import UTC, datetime
                from app.services.tracking_rules import timeframe_duration
                requested_at = datetime.now(UTC)
                df = await get_exchange(exchange).get_candles(provider_symbol, timeframe, limit)
                step = timeframe_duration(timeframe)
                df = df.copy()
                df["complete"] = [(value.to_pydatetime() if hasattr(value, "to_pydatetime") else value) + step <= requested_at for value in df["timestamp"]]
                df.attrs.update(requested_at=requested_at.isoformat(), fetched_at=datetime.now(UTC).isoformat(), timeframe=timeframe)
                return df
        except Exception as exc:
            last_error = exc
            logger.warning(
                "Candle fetch failed symbol=%s timeframe=%s exchange=%s attempt=%s/%s error=%s",
                symbol,
                timeframe,
                exchange,
                attempt,
                attempts,
                exc,
            )
            if attempt < attempts:
                await asyncio.sleep(base_delay * (2 ** (attempt - 1)))
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
            exchange_symbol = str(market.get("symbol", ""))
            normalized_markets.append(
                {
                    **market,
                    "symbol": exchange_symbol.upper(),
                    "exchange_symbol": exchange_symbol,
                    "exchange": normalized,
                    "active": market.get("active", True),
                    "volume": market.get("volume"),
                    "perpVolume24h": market.get("perpVolume24h", market.get("perp_volume_24h", market.get("volume_24h", market.get("volume")))),
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

        df = await _fetch_candles_with_retries(normalized_exchange, normalized_symbol, normalized_timeframe, int(limit))
        _candle_cache[key] = (monotonic(), df.copy())
        return df


def read_cached_candles(exchange, symbol, timeframe, limit):
    """Peek only: fast context reads never queue new provider requests."""
    now = monotonic()
    candidates = [(stamp, frame) for (venue, asset, interval, _), (stamp, frame) in _candle_cache.items()
                  if (venue, asset, interval) == (exchange.lower(), symbol.upper(), timeframe.lower())
                  and now-stamp < candle_ttl(timeframe) and len(frame) >= limit]
    if not candidates:
        return None
    _, frame = max(candidates, key=lambda item: item[0])
    return frame.tail(limit).copy()
