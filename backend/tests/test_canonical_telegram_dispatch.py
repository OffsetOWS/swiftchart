from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import sqlite3
from types import SimpleNamespace

from app.config import get_settings
from app.models.schemas import TradeIdea
from app.services.telegram_dispatch import telegram_dispatch_record
from app.services.trade_history import save_trade_ideas
from app.strategy.decision_engine import evaluate_strategy_decision
from app.utils import database


START = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)


class FakeBot:
    def __init__(self, failures: dict[int, int] | None = None) -> None:
        self.failures = dict(failures or {})
        self.messages: list[tuple[int, str]] = []

    async def send_message(self, chat_id: int, text: str, reply_markup=None):
        remaining = self.failures.get(chat_id, 0)
        if remaining:
            self.failures[chat_id] = remaining - 1
            raise RuntimeError("temporary Telegram failure")
        self.messages.append((chat_id, text))
        return SimpleNamespace(message_id=len(self.messages))


def configure_db(monkeypatch, tmp_path, subscribers: str = "101") -> None:
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'swiftchart.db'}")
    monkeypatch.setenv("BOT_STATE_PATH", str(tmp_path / "bot_state.json"))
    monkeypatch.setenv("ALERT_DEDUPE_STATE_PATH", str(tmp_path / "dedupe.json"))
    monkeypatch.setenv("TELEGRAM_ALERT_CHAT_IDS", subscribers)
    get_settings.cache_clear()
    database._INITIALIZED = False
    database.init_db()


def idea(
    regime: str = "RANGE_BOUND",
    *,
    candle_time: datetime = START,
    entry_status: str = "READY",
    entry_zone: tuple[float, float] = (100.0, 102.0),
) -> TradeIdea:
    candidate = TradeIdea(
        symbol="BTCUSDT",
        timeframe="4h",
        exchange="hyperliquid",
        direction="Long",
        market_regime=regime,
        entry_zone=entry_zone,
        stop_loss=95.0,
        take_profit_1=110.0,
        take_profit_2=118.0,
        risk_reward_ratio=2.5,
        reason="Canonical Telegram dispatch test.",
        confidence_score=88,
        setup_score=88,
        invalid_condition="Close below support.",
        entry_status=entry_status,
        signal_candle_time=candle_time,
        signal_candle_high=103.0,
        signal_candle_low=100.5,
        signal_candle_close=101.5,
        reversal_confirmations=["rejection at support"],
        regime_confidence_score=90,
    )
    evaluate_strategy_decision(candidate)
    return candidate


def save(candidate: TradeIdea) -> int:
    ids = save_trade_ideas([candidate])
    assert len(ids) == 1
    return ids[0]


def row(trade_id: int) -> dict:
    with database.get_connection() as connection:
        return dict(connection.execute("SELECT * FROM trade_ideas WHERE id = ?", (trade_id,)).fetchone())


def test_persisted_v2_trade_is_dispatched_and_durable(monkeypatch, tmp_path):
    from bot.alerts import run_alert_scan

    configure_db(monkeypatch, tmp_path)
    trade_id = save(idea())
    bot = FakeBot()

    result = asyncio.run(run_alert_scan(bot))

    assert result["source"] == "canonical_persisted_v2_opportunities"
    assert result["eligible"] == 1
    assert result["sent"] == 1
    assert len(bot.messages) == 1
    dispatch = telegram_dispatch_record(trade_id)
    assert dispatch["status"] == "SUCCEEDED"
    assert dispatch["opportunity_key"] == row(trade_id)["opportunity_key"]
    assert dispatch["recipients"][0]["status"] == "SENT"


def test_shadow_no_trade_and_wait_are_never_dispatched(monkeypatch, tmp_path):
    from bot.alerts import run_alert_scan

    configure_db(monkeypatch, tmp_path)
    shadow = idea("TRENDING_UP")
    no_trade = idea("BREAKOUT")
    waiting = idea(entry_status="WAIT_FOR_RETEST")
    assert [shadow.strategy_decision, no_trade.strategy_decision, waiting.strategy_decision] == [
        "SHADOW",
        "NO_TRADE",
        "WAIT_FOR_RETEST",
    ]
    trade_ids = [save(candidate) for candidate in (shadow, no_trade, waiting)]

    bot = FakeBot()
    result = asyncio.run(run_alert_scan(bot))

    assert result["eligible"] == 0
    assert bot.messages == []
    assert all(telegram_dispatch_record(trade_id) is None for trade_id in trade_ids)


def test_waiting_range_dispatches_only_after_valid_promotion(monkeypatch, tmp_path):
    from bot.alerts import run_alert_scan

    configure_db(monkeypatch, tmp_path)
    pending_id = save(idea(entry_status="WAIT_FOR_RETEST"))
    bot = FakeBot()
    assert asyncio.run(run_alert_scan(bot))["sent"] == 0

    confirmed = idea(candle_time=START + timedelta(minutes=30))
    assert confirmed.strategy_decision == "TRADE"
    save_trade_ideas([confirmed])
    assert asyncio.run(run_alert_scan(bot))["sent"] == 1

    stored = row(pending_id)
    assert stored["strategy_decision"] == "TRADE"
    assert stored["retest_confirmed_at"] is not None
    assert telegram_dispatch_record(pending_id)["status"] == "SUCCEEDED"


def test_same_opportunity_and_price_updates_do_not_resend(monkeypatch, tmp_path):
    from bot.alerts import run_alert_scan

    configure_db(monkeypatch, tmp_path)
    original = idea()
    trade_id = save(original)
    bot = FakeBot()
    assert asyncio.run(run_alert_scan(bot))["sent"] == 1

    updated = idea(entry_zone=(100.5, 102.5))
    assert updated.opportunity_key == original.opportunity_key
    assert save_trade_ideas([updated]) == [trade_id]
    assert asyncio.run(run_alert_scan(bot))["sent"] == 0
    assert len(bot.messages) == 1


def test_new_signal_candle_creates_new_alert_even_inside_legacy_cooldown(monkeypatch, tmp_path):
    from bot.alerts import run_alert_scan

    configure_db(monkeypatch, tmp_path)
    first_id = save(idea())
    bot = FakeBot()
    assert asyncio.run(run_alert_scan(bot))["sent"] == 1

    second = idea(candle_time=START + timedelta(hours=4))
    second_id = save(second)
    assert second_id != first_id
    assert asyncio.run(run_alert_scan(bot))["sent"] == 1
    assert len(bot.messages) == 2


def test_bot_restart_does_not_resend_successful_delivery(monkeypatch, tmp_path):
    from bot.alerts import run_alert_scan

    configure_db(monkeypatch, tmp_path)
    trade_id = save(idea())
    first_bot = FakeBot()
    assert asyncio.run(run_alert_scan(first_bot))["sent"] == 1

    database._INITIALIZED = False
    get_settings.cache_clear()
    restarted_bot = FakeBot()
    assert asyncio.run(run_alert_scan(restarted_bot))["sent"] == 0
    assert restarted_bot.messages == []
    assert telegram_dispatch_record(trade_id)["status"] == "SUCCEEDED"


def test_partial_delivery_retries_only_failed_subscriber(monkeypatch, tmp_path):
    from bot.alerts import run_alert_scan

    configure_db(monkeypatch, tmp_path, subscribers="101,202")
    trade_id = save(idea())
    bot = FakeBot(failures={202: 1})

    first = asyncio.run(run_alert_scan(bot))
    assert first["sent"] == 1
    assert first["failed"] == 1
    assert [chat_id for chat_id, _ in bot.messages] == [101]
    partial = telegram_dispatch_record(trade_id)
    assert partial["status"] == "PARTIAL"
    assert {recipient["chat_id"]: recipient["status"] for recipient in partial["recipients"]} == {
        "101": "SENT",
        "202": "FAILED_RETRYABLE",
    }

    second = asyncio.run(run_alert_scan(bot))
    assert second["sent"] == 1
    assert [chat_id for chat_id, _ in bot.messages] == [101, 202]
    assert telegram_dispatch_record(trade_id)["status"] == "SUCCEEDED"


def test_failed_dispatch_remains_retryable(monkeypatch, tmp_path):
    from bot.alerts import run_alert_scan

    configure_db(monkeypatch, tmp_path)
    trade_id = save(idea())
    bot = FakeBot(failures={101: 1})

    first = asyncio.run(run_alert_scan(bot))
    assert first["sent"] == 0
    assert first["failed"] == 1
    assert telegram_dispatch_record(trade_id)["status"] == "FAILED_RETRYABLE"

    second = asyncio.run(run_alert_scan(bot))
    assert second["sent"] == 1
    assert telegram_dispatch_record(trade_id)["status"] == "SUCCEEDED"


def test_dispatch_claim_lease_prevents_overlapping_poll_double_send(monkeypatch, tmp_path):
    from app.services.telegram_dispatch import claim_telegram_dispatches

    configure_db(monkeypatch, tmp_path)
    trade_id = save(idea())
    claimed_at = datetime.now(UTC)

    first = claim_telegram_dispatches([101], now=claimed_at)
    overlapping = claim_telegram_dispatches([101], now=claimed_at + timedelta(seconds=1))

    assert len(first) == 1
    assert overlapping == []
    assert telegram_dispatch_record(trade_id)["status"] == "ATTEMPTED"

    renewed = claim_telegram_dispatches([101], now=claimed_at + timedelta(minutes=6))
    assert len(renewed) == 1


def test_historical_pre_migration_opportunity_is_not_mass_sent(monkeypatch, tmp_path):
    from bot.alerts import run_alert_scan

    configure_db(monkeypatch, tmp_path)
    trade_id = save(idea())
    cutoff = datetime.now(UTC) + timedelta(minutes=1)
    with database.get_connection() as connection:
        connection.execute(
            "UPDATE telegram_dispatch_config SET canonical_started_at = ? WHERE id = 1",
            (cutoff.isoformat(),),
        )

    bot = FakeBot()
    assert asyncio.run(run_alert_scan(bot))["sent"] == 0
    assert bot.messages == []
    assert telegram_dispatch_record(trade_id) is None


def test_migration_cutoff_is_stable_across_reinitialization(monkeypatch, tmp_path):
    from app.services.telegram_dispatch import canonical_dispatch_cutoff

    configure_db(monkeypatch, tmp_path)
    initial = canonical_dispatch_cutoff()

    database._INITIALIZED = False
    database.init_db()

    assert canonical_dispatch_cutoff() == initial


def test_old_independent_scanner_cannot_double_dispatch(monkeypatch, tmp_path):
    from bot.alerts import run_alert_scan
    import bot.scanner as legacy_scanner

    configure_db(monkeypatch, tmp_path)
    save(idea())

    async def fail_if_called(*args, **kwargs):
        raise AssertionError("The independent bot scanner is not an actionable source")

    monkeypatch.setattr(legacy_scanner, "scan_top_ideas", fail_if_called)
    bot = FakeBot()
    assert asyncio.run(run_alert_scan(bot))["sent"] == 1


def test_production_crypto_subscriber_preferences_are_preserved(monkeypatch):
    from bot import alerts

    requested_markets: list[str] = []

    def preferred_subscribers(market: str):
        requested_markets.append(market)
        return {101, 202}

    monkeypatch.setattr(alerts, "get_subscribers", preferred_subscribers)

    assert alerts._crypto_subscribers() == {101, 202}
    assert requested_markets == ["crypto"]


def test_telegram_failure_does_not_change_execution_eligibility(monkeypatch, tmp_path):
    from bot.alerts import run_alert_scan

    configure_db(monkeypatch, tmp_path)
    trade_id = save(idea())
    before = row(trade_id)
    bot = FakeBot(failures={101: 1})

    assert asyncio.run(run_alert_scan(bot))["failed"] == 1

    after = row(trade_id)
    assert after["strategy_decision"] == before["strategy_decision"] == "TRADE"
    assert after["executable_at"] == before["executable_at"]
    assert after["lifecycle_status"] == before["lifecycle_status"] == "active"


def test_dispatch_migration_leaves_forex_table_and_row_unchanged(monkeypatch, tmp_path):
    db_path = tmp_path / "swiftchart.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    get_settings.cache_clear()
    database._INITIALIZED = False
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE forex_signals (id INTEGER PRIMARY KEY, pair TEXT, direction TEXT)")
        connection.execute("INSERT INTO forex_signals VALUES (1, 'EURUSD', 'LONG')")
    database.init_db()

    with sqlite3.connect(db_path) as connection:
        columns = [row[1] for row in connection.execute("PRAGMA table_info(forex_signals)")]
        rows = connection.execute("SELECT * FROM forex_signals").fetchall()
    assert columns == ["id", "pair", "direction"]
    assert rows == [(1, "EURUSD", "LONG")]
