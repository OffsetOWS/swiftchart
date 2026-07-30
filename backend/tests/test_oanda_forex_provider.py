from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import httpx
import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import get_settings
from app.forex.config import SUPPORTED_FOREX_PAIRS
from app.forex.oanda import (
    OANDA_GRANULARITIES,
    OandaForexProvider,
    oanda_instrument,
)
from app.forex.providers import (
    FallbackForexProvider,
    ForexDataProvider,
    ForexProviderError,
)


@pytest.fixture(autouse=True)
def reset_oanda_state():
    OandaForexProvider._unavailable_until = None
    OandaForexProvider._last_successful_request = None
    OandaForexProvider._last_error = None
    yield
    OandaForexProvider._unavailable_until = None
    OandaForexProvider._last_successful_request = None
    OandaForexProvider._last_error = None


def _response(status: int, payload: dict, path: str = "/candles") -> httpx.Response:
    return httpx.Response(
        status,
        request=httpx.Request("GET", f"https://api-fxpractice.oanda.com{path}"),
        json=payload,
    )


class ActionClient:
    def __init__(self, actions: list, calls: list[dict]) -> None:
        self.actions = actions
        self.calls = calls

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def get(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        action = self.actions.pop(0)
        if isinstance(action, Exception):
            raise action
        return action


def _provider(**kwargs) -> OandaForexProvider:
    return OandaForexProvider(
        api_key="private-test-token",
        account_id="101-test-account",
        environment="practice",
        base_url="https://api-fxpractice.oanda.com",
        retry_backoff_seconds=0,
        **kwargs,
    )


def test_oanda_authentication_and_account_health(monkeypatch):
    import app.forex.oanda as oanda

    calls: list[dict] = []
    actions = [_response(200, {"account": {"id": "101-test-account"}}, "/summary")]
    monkeypatch.setattr(
        oanda.httpx,
        "AsyncClient",
        lambda **kwargs: ActionClient(actions, calls),
    )

    health = asyncio.run(_provider().health())

    assert health["connected"] is True
    assert health["account_id"] == "101-test-account"
    assert health["environment"] == "practice"
    assert calls[0]["headers"]["Authorization"] == "Bearer private-test-token"
    assert "private-test-token" not in calls[0]["url"]
    assert calls[0]["url"].endswith("/v3/accounts/101-test-account/summary")


@pytest.mark.parametrize(
    ("swiftchart_timeframe", "granularity"),
    [("15m", "M15"), ("1h", "H1"), ("4h", "H4"), ("1d", "D")],
)
def test_oanda_timeframe_mapping(swiftchart_timeframe, granularity):
    assert OANDA_GRANULARITIES[swiftchart_timeframe] == granularity


@pytest.mark.parametrize(
    ("symbol", "instrument"),
    [
        ("EURUSD", "EUR_USD"),
        ("GBP/USD", "GBP_USD"),
        ("USD-JPY", "USD_JPY"),
        ("AUD_USD", "AUD_USD"),
        ("NZDUSD", "NZD_USD"),
        ("USDCAD", "USD_CAD"),
        ("USDCHF", "USD_CHF"),
        ("EURGBP", "EUR_GBP"),
        ("EURJPY", "EUR_JPY"),
        ("GBPJPY", "GBP_JPY"),
        ("XAUUSD", "XAU_USD"),
    ],
)
def test_oanda_symbol_mapping(symbol, instrument):
    assert oanda_instrument(symbol) == instrument


def test_oanda_candles_are_normalized_and_incomplete_candle_is_ignored(monkeypatch):
    import app.forex.oanda as oanda

    calls: list[dict] = []
    actions = [
        _response(
            200,
            {
                "candles": [
                    {
                        "complete": True,
                        "time": "2026-07-30T12:00:00.000000000Z",
                        "volume": 123,
                        "mid": {
                            "o": "1.14100",
                            "h": "1.14200",
                            "l": "1.14050",
                            "c": "1.14180",
                        },
                    },
                    {
                        "complete": False,
                        "time": "2026-07-30T12:15:00.000000000Z",
                        "volume": 10,
                        "mid": {
                            "o": "1.14180",
                            "h": "1.14210",
                            "l": "1.14170",
                            "c": "1.14200",
                        },
                    },
                ]
            },
        )
    ]
    monkeypatch.setattr(
        oanda.httpx,
        "AsyncClient",
        lambda **kwargs: ActionClient(actions, calls),
    )

    frame = asyncio.run(
        _provider().candles(SUPPORTED_FOREX_PAIRS["EURUSD"], "15m", 2)
    )

    assert list(frame.columns) == [
        "timestamp",
        "open",
        "high",
        "low",
        "close",
        "volume",
    ]
    assert len(frame) == 1
    assert frame.iloc[0]["timestamp"] == datetime(
        2026, 7, 30, 12, 0, tzinfo=UTC
    )
    assert frame.iloc[0]["close"] == 1.1418
    assert calls[0]["url"].endswith("/v3/instruments/EUR_USD/candles")
    assert calls[0]["params"]["granularity"] == "M15"
    assert calls[0]["params"]["price"] == "M"


def test_oanda_retries_timeout_then_succeeds(monkeypatch):
    import app.forex.oanda as oanda

    calls: list[dict] = []
    request = httpx.Request("GET", "https://api-fxpractice.oanda.com/candles")
    actions = [
        httpx.ReadTimeout("timed out", request=request),
        _response(
            200,
            {
                "candles": [
                    {
                        "complete": True,
                        "time": "2026-07-30T12:00:00Z",
                        "volume": 1,
                        "mid": {"o": "1", "h": "2", "l": "0.5", "c": "1.5"},
                    }
                ]
            },
        ),
    ]
    monkeypatch.setattr(
        oanda.httpx,
        "AsyncClient",
        lambda **kwargs: ActionClient(actions, calls),
    )

    frame = asyncio.run(
        _provider(retry_attempts=2).candles(
            SUPPORTED_FOREX_PAIRS["EURUSD"],
            "1h",
            1,
        )
    )

    assert len(calls) == 2
    assert len(frame) == 1


def test_oanda_rate_limit_opens_circuit_without_repeated_request(monkeypatch):
    import app.forex.oanda as oanda

    calls: list[dict] = []
    actions = [
        httpx.Response(
            429,
            request=httpx.Request(
                "GET", "https://api-fxpractice.oanda.com/candles"
            ),
            headers={"Retry-After": "120"},
            json={"errorMessage": "rate limit"},
        )
    ]
    monkeypatch.setattr(
        oanda.httpx,
        "AsyncClient",
        lambda **kwargs: ActionClient(actions, calls),
    )
    provider = _provider()

    with pytest.raises(ForexProviderError, match="rate limit"):
        asyncio.run(
            provider.candles(SUPPORTED_FOREX_PAIRS["EURUSD"], "15m", 1)
        )
    with pytest.raises(ForexProviderError, match="rate limit"):
        asyncio.run(
            provider.candles(SUPPORTED_FOREX_PAIRS["EURUSD"], "15m", 1)
        )

    assert len(calls) == 1


class StubProvider(ForexDataProvider):
    def __init__(self, name: str, result=None, error: Exception | None = None):
        self.name = name
        self.result = result
        self.error = error
        self.calls = 0

    async def candles(self, pair, timeframe: str, limit: int = 240):
        self.calls += 1
        if self.error:
            raise self.error
        return self.result


def test_fallback_provider_uses_twelve_data_after_oanda_failure():
    expected = pd.DataFrame(
        [
            {
                "timestamp": datetime(2026, 7, 30, tzinfo=UTC),
                "open": 1.0,
                "high": 1.1,
                "low": 0.9,
                "close": 1.05,
                "volume": 10,
            }
        ]
    )
    primary = StubProvider("oanda", error=ForexProviderError("OANDA unavailable"))
    fallback = StubProvider("twelvedata", result=expected)
    provider = FallbackForexProvider(primary, fallback)

    actual = asyncio.run(
        provider.candles(SUPPORTED_FOREX_PAIRS["EURUSD"], "15m", 1)
    )

    assert primary.calls == 1
    assert fallback.calls == 1
    pd.testing.assert_frame_equal(actual, expected)


def test_oanda_health_endpoint_is_internal_only(monkeypatch):
    from app.routes.forex import router

    monkeypatch.setenv("INTERNAL_API_SECRET", "server-only-secret")
    get_settings.cache_clear()

    async def fake_health(self):
        return {
            "provider": "oanda",
            "configured": True,
            "connected": True,
            "account_id": "101-test-account",
            "environment": "practice",
            "last_successful_request": None,
            "last_error": None,
        }

    monkeypatch.setattr(OandaForexProvider, "health", fake_health)
    app = FastAPI()
    app.include_router(router, prefix="/api")
    client = TestClient(app)

    assert client.get("/api/forex/provider/health").status_code == 403
    allowed = client.get(
        "/api/forex/provider/health",
        headers={"X-Internal-API-Secret": "server-only-secret"},
    )
    assert allowed.status_code == 200
    assert allowed.json()["connected"] is True
    assert "api_key" not in allowed.json()
    get_settings.cache_clear()
