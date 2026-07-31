from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import logging
from uuid import uuid4
from zoneinfo import ZoneInfo

import pandas as pd

from app.config import get_settings
from app.forex.config import (
    PROVIDER_TIMEFRAMES,
    ForexPairConfig,
    normalize_forex_timeframe,
)
from app.forex.providers import ForexDataProvider, get_forex_provider
from app.forex.storage import (
    acquire_market_data_lock,
    latest_forex_candle,
    list_forex_candles,
    release_market_data_lock,
    upsert_forex_candles,
)

logger = logging.getLogger(__name__)
NEW_YORK = ZoneInfo("America/New_York")
TIMEFRAME_SECONDS = {"15M": 900, "1H": 3600, "4H": 14_400, "1D": 86_400}


def forex_market_is_open(now: datetime | None = None) -> bool:
    current = (now or datetime.now(UTC)).astimezone(NEW_YORK)
    weekday = current.weekday()
    if weekday == 4 and current.hour >= 17:
        return False
    if weekday == 5:
        return False
    return not (weekday == 6 and current.hour < 17)


def latest_expected_candle_open(
    timeframe: str,
    now: datetime | None = None,
    *,
    delay_seconds: int | None = None,
) -> datetime:
    timeframe = normalize_forex_timeframe(timeframe)
    settings = get_settings()
    delayed = (now or datetime.now(UTC)) - timedelta(
        seconds=settings.forex_candle_close_delay_seconds
        if delay_seconds is None
        else delay_seconds
    )
    local = delayed.astimezone(NEW_YORK)
    if timeframe == "1H":
        close_local = local.replace(minute=0, second=0, microsecond=0)
        open_local = close_local - timedelta(hours=1)
    elif timeframe == "4H":
        anchors = (1, 5, 9, 13, 17, 21)
        close_hour = max((hour for hour in anchors if hour <= local.hour), default=21)
        close_day = local.date() if close_hour <= local.hour else (local - timedelta(days=1)).date()
        close_local = datetime.combine(close_day, datetime.min.time(), NEW_YORK).replace(
            hour=close_hour
        )
        open_local = close_local - timedelta(hours=4)
    else:
        close_local = local.replace(hour=17, minute=0, second=0, microsecond=0)
        if close_local > local:
            close_local -= timedelta(days=1)
        open_local = close_local - timedelta(days=1)
    return open_local.astimezone(UTC)


def seconds_until_next_candle_close(
    timeframe: str,
    now: datetime | None = None,
) -> float:
    timeframe = normalize_forex_timeframe(timeframe)
    current = now or datetime.now(UTC)
    expected_open = latest_expected_candle_open(timeframe, current)
    next_close = expected_open + timedelta(seconds=TIMEFRAME_SECONDS[timeframe] * 2)
    delay = get_settings().forex_candle_close_delay_seconds
    return max(1.0, (next_close + timedelta(seconds=delay) - current).total_seconds())


def _frame_from_rows(rows: list[dict]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
    return pd.DataFrame(
        [
            {
                "timestamp": pd.Timestamp(row["candle_open_at"]),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row["volume"]),
            }
            for row in rows
        ]
    )


class ForexMarketDataService:
    """Canonical cache-first path for all Forex candle consumers."""

    def __init__(self, provider: ForexDataProvider | None = None) -> None:
        self.provider = provider or get_forex_provider()

    async def health(self) -> dict:
        primary = getattr(self.provider, "primary", self.provider)
        health = getattr(primary, "health", None)
        if health is None:
            return {
                "provider": self.provider.name,
                "connected": True,
                "message": "Provider health is inferred from cached market data.",
            }
        return await health()

    async def completed_candles(
        self,
        pair: ForexPairConfig,
        timeframe: str,
        *,
        limit: int = 240,
        now: datetime | None = None,
    ) -> pd.DataFrame:
        timeframe = normalize_forex_timeframe(timeframe)
        current = now or datetime.now(UTC)
        expected = latest_expected_candle_open(timeframe, current)
        cached = latest_forex_candle(pair.pair, timeframe)
        if cached and datetime.fromisoformat(cached["candle_open_at"]) >= expected:
            return _frame_from_rows(list_forex_candles(pair.pair, timeframe, limit=limit))

        settings = get_settings()
        lock_key = f"forex-candle:{pair.pair}:{timeframe}:{expected.isoformat()}"
        owner = str(uuid4())
        deadline = asyncio.get_running_loop().time() + settings.forex_data_lock_timeout_seconds
        acquired = False
        while asyncio.get_running_loop().time() < deadline:
            acquired = acquire_market_data_lock(
                lock_key,
                owner,
                stale_seconds=settings.forex_data_lock_stale_seconds,
            )
            if acquired:
                break
            await asyncio.sleep(0.2)
            cached = latest_forex_candle(pair.pair, timeframe)
            if cached and datetime.fromisoformat(cached["candle_open_at"]) >= expected:
                return _frame_from_rows(
                    list_forex_candles(pair.pair, timeframe, limit=limit)
                )
        if not acquired:
            cached_rows = list_forex_candles(pair.pair, timeframe, limit=limit)
            if cached_rows:
                logger.warning("Forex candle lock timed out key=%s; serving stored data", lock_key)
                return _frame_from_rows(cached_rows)
            raise TimeoutError(f"Forex market-data synchronization timed out for {pair.pair} {timeframe}")

        try:
            cached = latest_forex_candle(pair.pair, timeframe)
            if cached and datetime.fromisoformat(cached["candle_open_at"]) >= expected:
                return _frame_from_rows(
                    list_forex_candles(pair.pair, timeframe, limit=limit)
                )
            fetch_limit = (
                settings.forex_bootstrap_candle_limit
                if cached is None
                else settings.forex_incremental_candle_limit
            )
            frame = await self.provider.candles(
                pair,
                PROVIDER_TIMEFRAMES[timeframe],
                fetch_limit,
            )
            source_provider = getattr(
                self.provider,
                "last_provider_name",
                self.provider.name,
            )
            fetched_at = datetime.now(UTC).isoformat()
            duration = timedelta(seconds=TIMEFRAME_SECONDS[timeframe])
            rows: list[dict] = []
            for item in frame.to_dict("records"):
                opened = pd.Timestamp(item["timestamp"]).to_pydatetime()
                opened = opened if opened.tzinfo else opened.replace(tzinfo=UTC)
                opened = opened.astimezone(UTC)
                if opened > expected:
                    continue
                rows.append(
                    {
                        "provider": source_provider,
                        "instrument": pair.provider_symbol.replace("/", "_"),
                        "symbol": pair.pair,
                        "timeframe": timeframe,
                        "candle_open_at": opened.isoformat(),
                        "candle_close_at": (opened + duration).isoformat(),
                        "open": float(item["open"]),
                        "high": float(item["high"]),
                        "low": float(item["low"]),
                        "close": float(item["close"]),
                        "volume": float(item.get("volume") or 0),
                        "complete": True,
                        "source_timestamp": opened.isoformat(),
                        "fetched_at": fetched_at,
                    }
                )
            upsert_forex_candles(rows)
            logger.info(
                "Forex candle sync provider=%s symbol=%s timeframe=%s fetched=%s expected=%s",
                source_provider,
                pair.pair,
                timeframe,
                len(rows),
                expected.isoformat(),
            )
            return _frame_from_rows(list_forex_candles(pair.pair, timeframe, limit=limit))
        finally:
            release_market_data_lock(lock_key, owner)


def get_forex_market_data_service() -> ForexMarketDataService:
    return ForexMarketDataService()
