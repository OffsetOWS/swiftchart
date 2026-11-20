from datetime import UTC, datetime

import pytest

from app.config import get_settings
from app.models.schemas import TradeIdea
from app.services.trade_history import list_active_crypto_trades, save_trade_ideas
from app.utils import database
from app.utils.database import get_connection
from app.strategy.decision_engine import evaluate_strategy_decision
from app.services.telegram_dispatch import claim_telegram_dispatches, mark_telegram_recipient_success


@pytest.fixture
def active_trade_db(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'active-trades.db'}")
    monkeypatch.setenv("ALERT_DEDUPE_STATE_PATH", str(tmp_path / "alert-dedupe.json"))
    get_settings.cache_clear()
    database._INITIALIZED = False
    database.init_db()
    yield
    database._INITIALIZED = False
    get_settings.cache_clear()


def crypto_idea(symbol: str = "ZECUSDT") -> TradeIdea:
    idea = TradeIdea(
        symbol=symbol,
        timeframe="4h",
        exchange="hyperliquid",
        direction="Short",
        market_regime="TRENDING_DOWN",
        entry_zone=(509.6, 514.65),
        stop_loss=517.67,
        take_profit_1=498.6,
        take_profit_2=493.56,
        risk_reward_ratio=2.9,
        reason="Canonical Telegram setup.",
        confidence_score=82,
        setup_score=82,
        invalid_condition="Close above resistance.",
        rank_score=82,
        signal_candle_time=datetime.now(UTC),
    )
    evaluate_strategy_decision(idea)
    return idea


def test_active_crypto_feed_returns_unresolved_telegram_trade(active_trade_db):
    ids = save_trade_ideas([crypto_idea()])
    assert list_active_crypto_trades() == []  # Persistence is not delivery.
    candidates = claim_telegram_dispatches({101})
    assert len(candidates) == 1
    mark_telegram_recipient_success(candidates[0].dispatch_id, 101, telegram_message_id=7)

    trades = list_active_crypto_trades()

    assert len(ids) == 1
    assert len(trades) == 1
    assert trades[0]["symbol"] == "ZECUSDT"
    assert trades[0]["source"] == "telegram-canonical"


def test_active_crypto_feed_excludes_expired_trade(active_trade_db):
    trade_id = save_trade_ideas([crypto_idea("ETHUSDT")])[0]
    with get_connection() as connection:
        connection.execute(
            "UPDATE trade_ideas SET expires_at = ? WHERE id = ?",
            ("2020-01-01T00:00:00+00:00", trade_id),
        )

    assert list_active_crypto_trades() == []
