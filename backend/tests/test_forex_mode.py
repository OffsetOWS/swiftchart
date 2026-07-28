from datetime import UTC, datetime, timedelta

import pandas as pd

from app.forex.config import SUPPORTED_FOREX_PAIRS
from app.forex.providers import ForexDataProvider, ForexProviderNotConfigured
from app.forex.scanner import scan_forex
from app.forex.sessions import forex_session_state


def candles(start: float = 1.08, step: float = 0.0004, rows: int = 90):
    now = datetime(2026, 6, 25, tzinfo=UTC)
    data = []
    price = start
    for idx in range(rows):
        price += step
        data.append(
            {
                "timestamp": now + timedelta(minutes=15 * idx),
                "open": price - step * 0.3,
                "high": price + abs(step) * 1.4,
                "low": price - abs(step) * 1.4,
                "close": price,
                "volume": 0,
            }
        )
    return pd.DataFrame(data)


class FakeForexProvider(ForexDataProvider):
    name = "fake"

    async def candles(self, pair, timeframe: str, limit: int = 240):
        step = 0.0005 if pair.pair != "USDJPY" else 0.04
        return candles(start=1.08 if pair.pair != "USDJPY" else 150.0, step=step, rows=100)


class MissingProvider(ForexDataProvider):
    name = "missing"

    async def candles(self, pair, timeframe: str, limit: int = 240):
        raise ForexProviderNotConfigured("Forex data provider is not configured.")


def test_forex_sessions_detect_london_new_york_overlap():
    state = forex_session_state(datetime(2026, 6, 25, 13, 30, tzinfo=UTC))

    assert state.active_session == "London-New York overlap"
    assert state.is_overlap is True
    assert state.market_open is True


def test_forex_scan_returns_clean_empty_when_provider_missing():
    result = __import__("asyncio").run(scan_forex(MissingProvider(), save=False))

    assert result.configured is False
    assert result.signals == []
    assert result.message == "Forex data provider is not configured."


def test_forex_scan_is_isolated_and_returns_supported_pairs(monkeypatch):
    monkeypatch.setenv("FOREX_NEWS_RISK", "LOW")
    result = __import__("asyncio").run(scan_forex(FakeForexProvider(), save=False))

    assert result.marketType == "forex"
    assert {pair.pair for pair in result.supportedPairs} == set(SUPPORTED_FOREX_PAIRS)
    assert result.signals
    assert all(signal.marketType == "forex" for signal in result.signals)
    assert all(signal.pair in SUPPORTED_FOREX_PAIRS for signal in result.signals)
