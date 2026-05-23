from __future__ import annotations

from datetime import UTC, datetime

from app.models.schemas import AnalysisResponse, MarketRegimeSnapshot, SignalReview, TradeIdea, Zone
from app.services import scanner


def regime(decision: str = "WAIT", block_reason: str | None = None) -> MarketRegimeSnapshot:
    return MarketRegimeSnapshot(
        score=12,
        label="Transition to Bullish",
        regime_type="TRANSITION_TO_BULLISH",
        confidence_score=58,
        structure="HL near support",
        is_transition=True,
        trade_decision=decision,
        bias="Bullish transition",
        long_bias="Possible",
        short_bias="Reduced",
        updated_at=datetime(2026, 5, 23, tzinfo=UTC),
        components={"regime_block_reason": block_reason},
    )


def analysis(**overrides) -> AnalysisResponse:
    data = {
        "symbol": "BTCUSDT",
        "timeframe": "4h",
        "exchange": "hyperliquid",
        "current_price": 100.0,
        "market_condition": "RANGE_BOUND",
        "support_zones": [Zone(type="support", lower=96, upper=101, strength=78, touches=4)],
        "resistance_zones": [Zone(type="resistance", lower=120, upper=124, strength=74, touches=3)],
        "liquidity_sweeps": [],
        "trade_ideas": [],
        "market_regime_data": regime(),
        "rejected_signals": [
            SignalReview(
                symbol="BTCUSDT",
                timeframe="4h",
                exchange="hyperliquid",
                direction="Long",
                accepted=False,
                reason="Long signal rejected because Transition to Bullish needs 2 confirmations before trading; found 1.",
                base_score=59,
                adjusted_score=59,
                regime_score=12,
                regime_label="Transition to Bullish",
                trend_alignment="counter-trend",
            )
        ],
        "no_trade_reason": "Unconfirmed sweep — no trade yet.",
    }
    data.update(overrides)
    return AnalysisResponse(**data)


def idea(**overrides) -> TradeIdea:
    data = {
        "symbol": "BTCUSDT",
        "timeframe": "4h",
        "exchange": "hyperliquid",
        "direction": "Long",
        "entry_zone": (100.0, 101.0),
        "stop_loss": 96.0,
        "take_profit_1": 110.0,
        "take_profit_2": 118.0,
        "risk_reward_ratio": 2.4,
        "reason": "Clean setup.",
        "confidence_score": 62,
        "setup_score": 62,
        "invalid_condition": "Invalid below support.",
        "entry_status": "WAIT_FOR_RETEST",
    }
    data.update(overrides)
    return TradeIdea(**data)


def test_watchlist_appears_for_wait_near_edge_candidate():
    item = scanner._watchlist_candidate_from_analysis(analysis())

    assert item is not None
    assert item["symbol"] == "BTCUSDT"
    assert item["label"] in {"Watching", "Unconfirmed sweep", "Needs confirmation"}


def test_hard_no_trade_does_not_become_watchlist():
    item = scanner._watchlist_candidate_from_analysis(
        analysis(market_regime_data=regime("NO_TRADE", "low_volatility"))
    )

    assert item is None


def test_wait_for_retest_idea_becomes_watchlist_not_ready():
    item = scanner._watchlist_candidate_from_idea(idea())

    assert item is not None
    assert item["label"] == "Waiting for retest"


def test_watchlist_does_not_trigger_telegram_diagnostics(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("TELEGRAM_ALERT_CHAT_IDS", "123")

    eligible, reasons = scanner._telegram_diagnostics([])

    assert eligible == 0
    assert reasons == {}


def test_ready_behavior_remains_unchanged():
    item = scanner._watchlist_candidate_from_idea(
        idea(entry_status="READY", setup_score=82, confidence_score=82, risk_reward_ratio=2.4)
    )

    assert item is None
