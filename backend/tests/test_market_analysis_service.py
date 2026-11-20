"""Core analysis regressions retained after removing the optional provider gateway."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from app.models.schemas import AnalysisResponse, MarketRegimeSnapshot, RiskSettings, TradeIdea


@pytest.fixture(autouse=True)
def development_settings(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


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


def _client() -> TestClient:
    return TestClient(app)


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
