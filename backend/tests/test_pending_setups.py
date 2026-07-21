from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pandas as pd
from fastapi.testclient import TestClient

from app.main import app
from app.models.schemas import AnalysisResponse, LiquiditySweep, MarketRegimeSnapshot, Zone
from app.services.pending_setups import build_pending_setup


def candles(close: float = 99.2) -> pd.DataFrame:
    rows = []
    base_time = datetime.now(UTC) - timedelta(hours=100)
    for index in range(100):
        level = 101 + index * 0.03
        rows.append(
            {
                "timestamp": base_time + timedelta(hours=index * 4),
                "open": level,
                "high": level + 1.2,
                "low": level - 1.1,
                "close": level + 0.2,
                "volume": 10_000 + index * 5,
            }
        )
    rows[-1].update({"open": 99.0, "high": 100.0, "low": 98.7, "close": close, "volume": 22_000})
    rows[-2].update({"open": 99.4, "high": 100.2, "low": 98.8, "close": 99.0, "volume": 18_000})
    return pd.DataFrame(rows)


def regime(
    *,
    regime_type: str = "RANGE_BOUND",
    trade_decision: str = "WAIT",
    label: str = "Range Environment",
    structure: str = "Range",
) -> MarketRegimeSnapshot:
    return MarketRegimeSnapshot(
        score=8,
        label=label,
        regime_type=regime_type,
        confidence_score=72,
        structure=structure,
        trade_decision=trade_decision,
        bias="Balanced",
        long_bias="Longs require confirmation",
        short_bias="Shorts require confirmation",
        updated_at=datetime.now(UTC),
    )


def analysis(*, market_regime: MarketRegimeSnapshot | None = None, sweeps: list[LiquiditySweep] | None = None) -> AnalysisResponse:
    return AnalysisResponse(
        symbol="TESTUSDT",
        timeframe="4h",
        exchange="hyperliquid",
        current_price=99.2,
        market_condition="RANGE_BOUND",
        support_zones=[Zone(type="support", lower=98.0, upper=99.0, strength=0.82, touches=4, strength_score=82)],
        resistance_zones=[Zone(type="resistance", lower=110.0, upper=111.0, strength=0.78, touches=4, strength_score=78)],
        liquidity_sweeps=sweeps or [],
        trade_ideas=[],
        market_regime_data=market_regime or regime(),
    )


def test_wait_near_edge_with_valid_hint_becomes_pending():
    setup = build_pending_setup(analysis(), candles())

    assert setup is not None
    assert setup.symbol == "TESTUSDT"
    assert setup.direction == "Long"
    assert setup.status in {"NEEDS_TRIGGER", "WAITING_FOR_RETEST"}
    assert "support" in " ".join(setup.trigger_hints)


def test_hard_no_trade_does_not_become_pending():
    setup = build_pending_setup(
        analysis(market_regime=regime(trade_decision="NO_TRADE", label="High Risk Chop", structure="Mixed / Choppy")),
        candles(),
    )

    assert setup is None


def test_trending_continuation_watch_requires_valid_structure():
    valid = build_pending_setup(
        analysis(market_regime=regime(regime_type="TRENDING_UP", trade_decision="WAIT", label="Strong Bull Trend", structure="HH/HL")),
        candles(),
    )
    invalid = build_pending_setup(
        AnalysisResponse(
            **{
                **analysis(market_regime=regime(regime_type="TRENDING_UP", structure="Insufficient structure")).model_dump(),
                "support_zones": [],
                "resistance_zones": [],
            }
        ),
        candles(),
    )

    assert valid is not None
    assert valid.status == "CONTINUATION_WATCH"
    assert invalid is None


def test_top_ideas_returns_confirmed_and_pending(monkeypatch):
    from app.routes import markets
    from app.models.schemas import TradeIdea

    confirmed = TradeIdea(
        symbol="BTCUSDT",
        timeframe="4h",
        exchange="hyperliquid",
        direction="Long",
        entry_zone=(100.0, 101.0),
        stop_loss=96.0,
        take_profit_1=110.0,
        take_profit_2=118.0,
        risk_reward_ratio=2.5,
        reason="Confirmed setup.",
        confidence_score=82,
        setup_score=82,
        invalid_condition="Close below support.",
        rank_score=82,
    )
    pending = build_pending_setup(analysis(), candles())

    async def fake_cached_top_ideas(exchange: str, timeframe: str):
        return {"exchange": exchange, "timeframe": timeframe, "ideas": [confirmed], "pending_setups": [pending], "scan_stats": {}}

    monkeypatch.setattr(markets, "cached_top_ideas", fake_cached_top_ideas)

    result = asyncio.run(markets.top_ideas(exchange="hyperliquid", timeframe="4h", symbols=None))

    assert result["ideas"] == [confirmed]
    assert result["pending_setups"] == [pending]


def test_pending_setup_does_not_trigger_telegram(monkeypatch, tmp_path):
    from bot.alerts import run_alert_scan
    from app.config import get_settings
    from app.utils import database

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'swiftchart.db'}")
    monkeypatch.setenv("BOT_STATE_PATH", str(tmp_path / "bot_state.json"))
    monkeypatch.setenv("ALERT_DEDUPE_STATE_PATH", str(tmp_path / "alert_dedupe.json"))
    monkeypatch.setenv("TELEGRAM_ALERT_CHAT_IDS", "123")
    get_settings.cache_clear()
    database._INITIALIZED = False
    database.init_db()

    class FakeBot:
        messages: list[str] = []

        async def send_message(self, chat_id: int, text: str) -> None:
            self.messages.append(text)

    result = asyncio.run(run_alert_scan(FakeBot()))

    assert result["ideas"] == 0
    assert result["sent"] == 0


def test_pending_setup_is_not_dispatched_to_executor(monkeypatch):
    from app.services import scanner

    df = candles()
    monkeypatch.setattr(scanner, "_scan_cache", {})
    async def fake_discover_all_scan_markets(exchange):
        return [{"exchange": "hyperliquid", "symbol": "TESTUSDT", "exchange_symbol": "TESTUSDT"}]

    monkeypatch.setattr(scanner, "discover_all_scan_markets", fake_discover_all_scan_markets)
    monkeypatch.setattr(scanner, "selected_exchanges", lambda exchange: ["hyperliquid"])

    async def fake_prefilter_market(market, timeframe, semaphore, stats):
        return scanner.Candidate("hyperliquid", "TESTUSDT", "TESTUSDT", df, 1000, 0.4)

    def fake_analyze_dataframe(*args, **kwargs):
        return analysis()

    dispatched: list[list] = []

    async def fake_dispatch(ideas):
        dispatched.append(ideas)

    monkeypatch.setattr(scanner, "_prefilter_market", fake_prefilter_market)
    monkeypatch.setattr(scanner, "analyze_dataframe", fake_analyze_dataframe)
    monkeypatch.setattr(scanner, "save_signal_reviews", lambda reviews: None)
    monkeypatch.setattr(scanner, "save_trade_ideas", lambda ideas: [])
    monkeypatch.setattr(scanner, "dispatch_trade_ideas_to_execution", fake_dispatch)

    result = asyncio.run(scanner.run_scan(exchange="hyperliquid", timeframe="4h", force=True))

    assert result["ideas"] == []
    assert result["pending_setups"]
    assert dispatched == [[]]


def test_pending_shape_cannot_be_saved_as_paper_trade():
    pending = build_pending_setup(analysis(), candles())
    assert pending is not None
    response = TestClient(app).post("/api/paper-trade", json=pending.model_dump(mode="json"))

    assert response.status_code == 422
