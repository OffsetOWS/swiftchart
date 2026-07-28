from __future__ import annotations

import asyncio
import importlib.util
import os
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

os.environ["ENVIRONMENT"] = "development"
os.environ["OKX_X402_ENABLED"] = "false"

from app.config import get_settings
from app.exchanges.base import MarketDataUnavailable
from app.integrations.okx_asp.auth import reset_okx_asp_rate_limit_for_tests
from app.main import app
from app.models.schemas import AnalysisResponse, MarketRegimeSnapshot, RiskSettings, TradeIdea


API_KEY = "okx-test-key"
HEADERS = {"X-SwiftChart-ASP-Key": API_KEY}


def _forbid_optional_execution_services(monkeypatch, forbidden):
    for module, target in (
        ("app.mt5.service", "app.mt5.service.ForexExecutionService.open_trade"),
        ("app.ea.service", "app.ea.service.EAExecutionService.receive_signal"),
    ):
        try:
            installed = importlib.util.find_spec(module) is not None
        except ModuleNotFoundError:
            installed = False
        if installed:
            monkeypatch.setattr(target, forbidden)


def _regime(regime_type: str = "TRENDING_UP") -> MarketRegimeSnapshot:
    bullish = regime_type in {"TRENDING_UP", "BREAKOUT", "TRANSITION_TO_BULLISH"}
    bearish = regime_type in {"TRENDING_DOWN", "BREAKDOWN", "TRANSITION_TO_BEARISH"}
    return MarketRegimeSnapshot(
        score=72 if bullish else -72 if bearish else 0,
        label="Strong Bullish" if bullish else "Strong Bearish" if bearish else "Ranging / Neutral",
        regime_type=regime_type,
        confidence_score=84,
        structure="HH/HL" if bullish else "LH/LL" if bearish else "Range",
        trade_decision="TRADE_ALLOWED" if bullish or bearish else "NO_TRADE",
        bias="Long bias" if bullish else "Short bias" if bearish else "Balanced",
        long_bias="Prioritize longs" if bullish else "Range longs allowed",
        short_bias="Prioritize shorts" if bearish else "Range shorts allowed",
        bias_reason="Bias derived from the existing SwiftChart regime engine.",
        updated_at=datetime.now(UTC),
        explanation="Real SwiftChart market-regime explanation.",
    )


def _analysis(*, with_trade: bool = True) -> AnalysisResponse:
    regime = _regime("TRENDING_UP" if with_trade else "CHOP")
    ideas = []
    if with_trade:
        ideas = [
            TradeIdea(
                symbol="BTCUSDT",
                timeframe="4h",
                exchange="hyperliquid",
                direction="Long",
                market_regime="TRENDING_UP",
                setup_grade="A+ Setup",
                setup_score=87,
                entry_zone=(100.0, 102.0),
                stop_loss=96.0,
                take_profit_1=110.0,
                take_profit_2=118.0,
                risk_reward_ratio=4.25,
                reason="Existing SwiftChart setup reasoning.",
                confidence_score=87,
                invalid_condition="Invalid below 96.",
                rank_score=95,
                reversal_confirmations=["bullish momentum confirmation"],
            )
        ]
    return AnalysisResponse(
        symbol="BTCUSDT",
        timeframe="4h",
        exchange="hyperliquid",
        current_price=101,
        market_condition=regime.regime_type,
        support_zones=[],
        resistance_zones=[],
        liquidity_sweeps=[],
        trade_ideas=ideas,
        warning=None if with_trade else "NO TRADE: market is choppy.",
        no_trade_reason=None if with_trade else "NO TRADE: market is choppy.",
        market_regime_data=regime,
    )


@pytest.fixture(autouse=True)
def configure_okx_asp(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("OKX_ASP_API_KEY", API_KEY)
    monkeypatch.setenv("OKX_ASP_RATE_LIMIT_PER_MINUTE", "30")
    monkeypatch.setenv("OKX_X402_ENABLED", "false")
    get_settings.cache_clear()
    reset_okx_asp_rate_limit_for_tests()
    yield
    get_settings.cache_clear()
    reset_okx_asp_rate_limit_for_tests()


def _client() -> TestClient:
    return TestClient(app)


def test_valid_analysis_returns_top_ranked_real_idea(monkeypatch):
    async def fake_read_only_analysis(**_kwargs):
        return _analysis(with_trade=True)

    monkeypatch.setattr("app.integrations.okx_asp.service.analyze_market_read_only", fake_read_only_analysis)
    response = _client().post("/api/asp/okx/analyze-market", headers=HEADERS, json={"symbol": "BTC", "timeframe": "4h"})

    assert response.status_code == 200
    assert response.json() == {
        "symbol": "BTC",
        "timeframe": "4h",
        "status": "TRADE",
        "direction": "LONG",
        "score": 87.0,
        "grade": "A+ Setup",
        "entry": {"low": 100.0, "high": 102.0},
        "stopLoss": 96.0,
        "takeProfit1": 110.0,
        "takeProfit2": 118.0,
        "riskReward": 4.25,
        "marketBias": "BULLISH",
        "reasons": ["Existing SwiftChart setup reasoning.", "bullish momentum confirmation"],
    }


def test_no_trade_response_uses_null_levels_and_real_context(monkeypatch):
    async def fake_read_only_analysis(**_kwargs):
        return _analysis(with_trade=False)

    monkeypatch.setattr("app.integrations.okx_asp.service.analyze_market_read_only", fake_read_only_analysis)
    response = _client().post("/api/asp/okx/analyze-market", headers=HEADERS, json={"symbol": "BTC", "timeframe": "4h"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "NO_TRADE"
    assert payload["direction"] is None
    assert payload["score"] is None
    assert payload["grade"] == "No Trade"
    assert payload["entry"] is None
    assert payload["stopLoss"] is None
    assert payload["takeProfit1"] is None
    assert payload["takeProfit2"] is None
    assert payload["riskReward"] is None
    assert payload["marketBias"] == "NEUTRAL"
    assert payload["reasons"] == [
        "NO TRADE: market is choppy.",
        "Bias derived from the existing SwiftChart regime engine.",
        "Real SwiftChart market-regime explanation.",
    ]


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"symbol": "BTC<script>", "timeframe": "4h"}, "Symbol may contain only"),
        ({"symbol": "BTC", "timeframe": "5m"}, "Unsupported timeframe"),
    ],
)
def test_invalid_input_is_rejected(payload, message):
    response = _client().post("/api/asp/okx/analyze-market", headers=HEADERS, json=payload)

    assert response.status_code == 422
    assert message in response.text


def test_missing_api_key_is_rejected():
    response = _client().post("/api/asp/okx/analyze-market", json={"symbol": "BTC", "timeframe": "4h"})

    assert response.status_code == 401


def test_invalid_api_key_is_rejected():
    response = _client().post(
        "/api/asp/okx/analyze-market",
        headers={"X-SwiftChart-ASP-Key": "wrong-key"},
        json={"symbol": "BTC", "timeframe": "4h"},
    )

    assert response.status_code == 401


def test_upstream_market_data_failure_returns_503(monkeypatch):
    async def fail_analysis(**_kwargs):
        raise MarketDataUnavailable("Market data is temporarily unavailable.")

    monkeypatch.setattr("app.integrations.okx_asp.service.analyze_market_read_only", fail_analysis)
    response = _client().post("/api/asp/okx/analyze-market", headers=HEADERS, json={"symbol": "BTC", "timeframe": "4h"})

    assert response.status_code == 503
    assert "temporarily unavailable" in response.text


def test_dedicated_rate_limit(monkeypatch):
    monkeypatch.setenv("OKX_ASP_RATE_LIMIT_PER_MINUTE", "1")
    get_settings.cache_clear()

    async def fake_read_only_analysis(**_kwargs):
        return _analysis(with_trade=True)

    monkeypatch.setattr("app.integrations.okx_asp.service.analyze_market_read_only", fake_read_only_analysis)
    client = _client()
    first = client.post("/api/asp/okx/analyze-market", headers=HEADERS, json={"symbol": "BTC", "timeframe": "4h"})
    second = client.post("/api/asp/okx/analyze-market", headers=HEADERS, json={"symbol": "BTC", "timeframe": "4h"})

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.headers["Retry-After"]


def test_public_gateway_uses_same_read_only_analysis_service(monkeypatch):
    async def fake_read_only_analysis(**_kwargs):
        return _analysis(with_trade=False)

    monkeypatch.setattr("app.integrations.okx_asp.service.analyze_market_read_only", fake_read_only_analysis)
    response = _client().post(
        "/api/asp/okx/public/analyze-market",
        headers={"Content-Type": "application/json"},
        json={"symbol": "BTC", "timeframe": "4h"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "NO_TRADE"
    assert response.json()["entry"] is None


def test_public_gateway_has_strict_input_and_body_size_limits(monkeypatch):
    async def should_not_analyze(**_kwargs):
        raise AssertionError("Invalid public request reached the analysis engine")

    monkeypatch.setattr("app.integrations.okx_asp.service.analyze_market_read_only", should_not_analyze)
    invalid = _client().post(
        "/api/asp/okx/public/analyze-market",
        headers={"Content-Type": "application/json"},
        json={"symbol": "BTC", "timeframe": "4h", "exchange": "all"},
    )
    oversized = _client().post(
        "/api/asp/okx/public/analyze-market",
        headers={"Content-Type": "application/json"},
        content=b"{" + b" " * 1100 + b"}",
    )

    assert invalid.status_code == 422
    assert oversized.status_code == 413


def test_public_gateway_accepts_valid_json_without_content_type(monkeypatch):
    async def fake_read_only_analysis(**_kwargs):
        return _analysis(with_trade=False)

    monkeypatch.setattr("app.integrations.okx_asp.service.analyze_market_read_only", fake_read_only_analysis)
    response = _client().post(
        "/api/asp/okx/public/analyze-market",
        content=b'{"symbol":"BTC","timeframe":"4h"}',
    )

    assert response.status_code == 200
    assert response.json()["status"] == "NO_TRADE"


def test_public_gateway_rejects_explicit_non_json_content_type(monkeypatch):
    async def should_not_analyze(**_kwargs):
        raise AssertionError("Non-JSON public request reached the analysis engine")

    monkeypatch.setattr("app.integrations.okx_asp.service.analyze_market_read_only", should_not_analyze)
    response = _client().post(
        "/api/asp/okx/public/analyze-market",
        headers={"Content-Type": "text/plain"},
        content=b'{"symbol":"BTC","timeframe":"4h"}',
    )

    assert response.status_code == 415


def test_public_gateway_has_dedicated_rate_limit(monkeypatch):
    monkeypatch.setenv("OKX_ASP_PUBLIC_RATE_LIMIT_PER_MINUTE", "1")
    get_settings.cache_clear()

    async def fake_read_only_analysis(**_kwargs):
        return _analysis(with_trade=False)

    monkeypatch.setattr("app.integrations.okx_asp.service.analyze_market_read_only", fake_read_only_analysis)
    client = _client()
    first = client.post(
        "/api/asp/okx/public/analyze-market",
        headers={"Content-Type": "application/json"},
        json={"symbol": "BTC", "timeframe": "4h"},
    )
    second = client.post(
        "/api/asp/okx/public/analyze-market",
        headers={"Content-Type": "application/json"},
        json={"symbol": "BTC", "timeframe": "4h"},
    )

    assert first.status_code == 200
    assert second.status_code == 429


def test_asp_request_cannot_call_delivery_persistence_or_execution_systems(monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("ASP crossed a forbidden side-effect boundary")

    async def forbidden_async(*_args, **_kwargs):
        raise AssertionError("ASP crossed a forbidden async side-effect boundary")

    async def fake_read_only_analysis(**_kwargs):
        return _analysis(with_trade=True)

    monkeypatch.setattr("app.integrations.okx_asp.service.analyze_market_read_only", fake_read_only_analysis)
    monkeypatch.setattr("app.services.trade_history.save_trade_ideas", forbidden)
    monkeypatch.setattr("app.services.trade_history.save_signal_reviews", forbidden)
    monkeypatch.setattr("app.services.execution_signals.dispatch_trade_ideas_to_execution", forbidden_async)
    monkeypatch.setattr("app.routes.paper_trades.create_paper_trade", forbidden_async)
    monkeypatch.setattr("app.forex.scanner.scan_forex", forbidden_async)
    _forbid_optional_execution_services(monkeypatch, forbidden)
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2]))
    monkeypatch.setattr("bot.alerts.run_alert_scan", forbidden_async)

    response = _client().post("/api/asp/okx/analyze-market", headers=HEADERS, json={"symbol": "BTC", "timeframe": "4h"})

    assert response.status_code == 200


def test_public_request_cannot_call_delivery_persistence_or_execution_systems(monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("Public ASP crossed a forbidden side-effect boundary")

    async def forbidden_async(*_args, **_kwargs):
        raise AssertionError("Public ASP crossed a forbidden async side-effect boundary")

    async def fake_read_only_analysis(**_kwargs):
        return _analysis(with_trade=True)

    monkeypatch.setattr("app.integrations.okx_asp.service.analyze_market_read_only", fake_read_only_analysis)
    monkeypatch.setattr("app.services.trade_history.save_trade_ideas", forbidden)
    monkeypatch.setattr("app.services.trade_history.save_signal_reviews", forbidden)
    monkeypatch.setattr("app.services.execution_signals.dispatch_trade_ideas_to_execution", forbidden_async)
    monkeypatch.setattr("app.routes.paper_trades.create_paper_trade", forbidden_async)
    monkeypatch.setattr("app.forex.scanner.scan_forex", forbidden_async)
    _forbid_optional_execution_services(monkeypatch, forbidden)
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2]))
    monkeypatch.setattr("bot.alerts.run_alert_scan", forbidden_async)

    response = _client().post(
        "/api/asp/okx/public/analyze-market",
        headers={"Content-Type": "application/json"},
        json={"symbol": "BTC", "timeframe": "4h"},
    )

    assert response.status_code == 200


def test_public_gateway_returns_bounded_timeout(monkeypatch):
    monkeypatch.setenv("OKX_ASP_ANALYSIS_TIMEOUT_SECONDS", "1")
    get_settings.cache_clear()

    async def slow_analysis(**_kwargs):
        await asyncio.sleep(2)

    monkeypatch.setattr("app.integrations.okx_asp.service.analyze_market_read_only", slow_analysis)
    response = _client().post(
        "/api/asp/okx/public/analyze-market",
        headers={"Content-Type": "application/json"},
        json={"symbol": "BTC", "timeframe": "4h"},
    )

    assert response.status_code == 504
    assert response.json() == {"detail": "SwiftChart analysis timed out."}


def test_existing_analyze_route_still_persists_ideas_and_reviews(monkeypatch):
    analysis = _analysis(with_trade=True)
    saved_ideas = None
    saved_reviews = None

    async def fake_read_only_analysis(**_kwargs):
        return analysis

    def fake_save_ideas(ideas):
        nonlocal saved_ideas
        saved_ideas = ideas
        return [123]

    def fake_save_reviews(reviews):
        nonlocal saved_reviews
        saved_reviews = reviews
        return len(reviews)

    monkeypatch.setattr("app.routes.markets.analyze_market_read_only", fake_read_only_analysis)
    monkeypatch.setattr("app.routes.markets.save_trade_ideas", fake_save_ideas)
    monkeypatch.setattr("app.routes.markets.save_signal_reviews", fake_save_reviews)

    response = _client().get("/api/analyze?exchange=hyperliquid&symbol=BTCUSDT&timeframe=4h")

    assert response.status_code == 200
    assert response.json()["symbol"] == "BTCUSDT"
    assert saved_ideas is analysis.trade_ideas
    assert saved_reviews is analysis.rejected_signals


def test_shared_service_calls_existing_analysis_engine(monkeypatch):
    from app.services import market_analysis

    candles = pd.DataFrame(
        {
            "timestamp": pd.date_range("2026-01-01", periods=80, freq="4h", tz="UTC"),
            "open": range(80),
            "high": [value + 2 for value in range(80)],
            "low": [max(0, value - 1) for value in range(80)],
            "close": [value + 1 for value in range(80)],
            "volume": [1_000_000] * 80,
        }
    )
    expected = _analysis(with_trade=True)
    called = False

    async def fake_markets(_exchange):
        return [{"symbol": "BTCUSDT", "perpVolume24h": 10_000_000}]

    async def fake_candles(*_args):
        return candles

    def fake_engine(*_args, **_kwargs):
        nonlocal called
        called = True
        return expected

    async def fake_global_regime_score(*_args):
        return None

    monkeypatch.setattr(market_analysis, "get_markets_cached", fake_markets)
    monkeypatch.setattr(market_analysis, "get_candles_cached", fake_candles)
    monkeypatch.setattr(market_analysis, "analyze_dataframe", fake_engine)
    monkeypatch.setattr(market_analysis, "global_regime_score", fake_global_regime_score)

    result = asyncio.run(
        market_analysis.analyze_market_read_only(
            exchange="hyperliquid",
            symbol="BTC",
            timeframe="4h",
            risk=RiskSettings(),
        )
    )

    assert called is True
    assert result is expected
