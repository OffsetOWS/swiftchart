from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

from app.config import get_settings
from app.forex.config import SUPPORTED_FOREX_PAIRS, enabled_forex_timeframes
from app.forex.market_data import ForexMarketDataService, latest_expected_candle_open
from app.forex.providers import ForexDataProvider
from app.forex.storage import list_forex_candles


class CountingProvider(ForexDataProvider):
    name = "oanda"

    def __init__(self, end: datetime) -> None:
        self.end = end
        self.calls = 0

    async def candles(self, pair, timeframe: str, limit: int = 240) -> pd.DataFrame:
        self.calls += 1
        await asyncio.sleep(0.05)
        spacing = {"1h": 1, "4h": 4, "1d": 24}[timeframe]
        return pd.DataFrame(
            [
                {
                    "timestamp": self.end - timedelta(hours=spacing * index),
                    "open": 1.1,
                    "high": 1.2,
                    "low": 1.0,
                    "close": 1.15,
                    "volume": 100,
                }
                for index in reversed(range(min(limit, 100)))
            ]
        )


@pytest.fixture()
def candle_database(monkeypatch, tmp_path: Path):
    import app.utils.database as database

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'candles.db'}")
    monkeypatch.setenv("FOREX_ENABLED_TIMEFRAMES", "1H,4H,1D")
    get_settings.cache_clear()
    database._INITIALIZED = False
    yield
    get_settings.cache_clear()
    database._INITIALIZED = False


def test_active_timeframes_exclude_15m(candle_database):
    assert enabled_forex_timeframes() == ("1H", "4H", "1D")


def test_repeated_and_concurrent_reads_use_one_provider_request(candle_database):
    now = datetime(2026, 7, 30, 12, 30, tzinfo=UTC)
    expected = latest_expected_candle_open("1H", now, delay_seconds=15)
    provider = CountingProvider(expected)
    pair = SUPPORTED_FOREX_PAIRS["EURUSD"]

    async def run():
        first, second = await asyncio.gather(
            ForexMarketDataService(provider).completed_candles(
                pair, "1H", limit=60, now=now
            ),
            ForexMarketDataService(provider).completed_candles(
                pair, "1H", limit=60, now=now
            ),
        )
        third = await ForexMarketDataService(provider).completed_candles(
            pair, "1H", limit=60, now=now
        )
        return first, second, third

    frames = asyncio.run(run())
    assert provider.calls == 1
    assert all(not frame.empty for frame in frames)
    stored = list_forex_candles("EURUSD", "1H", limit=500)
    assert len(stored) == len({row["candle_open_at"] for row in stored})
