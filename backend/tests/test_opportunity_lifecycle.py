from __future__ import annotations

import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta

from app.config import get_settings
from app.models.schemas import TradeHistoryRecord, TradeIdea
from app.services.trade_history import check_trade_outcomes, get_trade_history, save_trade_ideas, stats
from app.services.alert_dedupe import mark_alert_sent, should_skip_alert
from app.utils import database
from app.utils.opportunities import canonical_opportunity_key


SIGNAL_TIME = datetime(2026, 7, 20, 8, 0, tzinfo=UTC)


def idea(
    *,
    direction: str = "Long",
    regime: str = "TRENDING_UP",
    candle_time: datetime = SIGNAL_TIME,
    entry_status: str = "READY",
    entry_zone: tuple[float, float] = (100.0, 102.0),
    stop: float = 96.0,
    tp1: float = 108.0,
    tp2: float = 116.0,
    candle_high: float = 103.0,
    candle_low: float = 99.0,
    candle_close: float = 102.0,
    confirmations: list[str] | None = None,
) -> TradeIdea:
    return TradeIdea(
        symbol="BTCUSDT",
        timeframe="4h",
        exchange="hyperliquid",
        direction=direction,
        market_regime=regime,
        entry_zone=entry_zone,
        stop_loss=stop,
        take_profit_1=tp1,
        take_profit_2=tp2,
        risk_reward_ratio=2.5,
        reason="Opportunity lifecycle test.",
        confidence_score=82,
        setup_score=82,
        setup_grade="A+ Setup",
        invalid_condition="Invalid beyond stop.",
        entry_status=entry_status,
        signal_candle_time=candle_time,
        signal_candle_high=candle_high,
        signal_candle_low=candle_low,
        signal_candle_close=candle_close,
        reversal_confirmations=confirmations or [],
    )


def configure_db(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'swiftchart.db'}")
    monkeypatch.setenv("ALERT_DEDUPE_STATE_PATH", str(tmp_path / "dedupe.json"))
    get_settings.cache_clear()
    database._INITIALIZED = False


def rows():
    with database.get_connection() as connection:
        return [dict(row) for row in connection.execute("SELECT * FROM trade_ideas ORDER BY id").fetchall()]


def test_same_opportunity_updates_instead_of_inserting(monkeypatch, tmp_path):
    configure_db(monkeypatch, tmp_path)
    first = idea()
    changed = idea(entry_zone=(100.25, 102.25), stop=95.75, tp1=108.25, tp2=116.25)

    save_trade_ideas([first])
    save_trade_ideas([changed])

    stored = rows()
    assert len(stored) == 1
    assert stored[0]["entry_zone_low"] == 100.25
    assert stored[0]["stop_loss"] == 95.75
    assert stored[0]["opportunity_key"] == canonical_opportunity_key(
        exchange="hyperliquid",
        symbol="BTCUSDT",
        timeframe="4h",
        direction="Long",
        setup_family="trend_continuation",
        signal_candle_time=SIGNAL_TIME,
    )


def test_next_candle_is_a_new_opportunity(monkeypatch, tmp_path):
    configure_db(monkeypatch, tmp_path)
    save_trade_ideas([idea()])
    save_trade_ideas([idea(candle_time=SIGNAL_TIME + timedelta(hours=4))])
    assert len(rows()) == 2


def test_long_short_and_setup_families_remain_separate(monkeypatch, tmp_path):
    configure_db(monkeypatch, tmp_path)
    save_trade_ideas(
        [
            idea(direction="Long", regime="TRENDING_UP"),
            idea(direction="Short", regime="TRENDING_DOWN", candle_close=100.0),
            idea(direction="Long", regime="BREAKOUT"),
        ]
    )
    stored = rows()
    assert len(stored) == 3
    assert {row["direction"] for row in stored} == {"LONG", "SHORT"}
    assert {row["setup_family"] for row in stored} == {"trend_continuation", "breakout"}


def test_completed_opportunity_is_never_overwritten(monkeypatch, tmp_path):
    configure_db(monkeypatch, tmp_path)
    save_trade_ideas([idea()])
    with database.get_connection() as connection:
        connection.execute(
            "UPDATE trade_ideas SET status='SL_HIT', result='LOSS', pnl_r_multiple=-1, closed_at=?",
            ((SIGNAL_TIME + timedelta(hours=8)).isoformat(),),
        )

    save_trade_ideas([idea(entry_zone=(110.0, 112.0), stop=105.0)])
    stored = rows()
    assert len(stored) == 1
    assert stored[0]["entry_zone_low"] == 100.0
    assert stored[0]["result"] == "LOSS"
    assert stored[0]["pnl_r_multiple"] == -1


def test_wait_for_retest_is_pending_and_excluded_from_outcomes_and_stats(monkeypatch, tmp_path):
    configure_db(monkeypatch, tmp_path)
    save_trade_ideas([idea(entry_status="WAIT_FOR_RETEST")])

    stored = rows()[0]
    snapshot = stats()
    outcome_check = asyncio.run(check_trade_outcomes())

    assert stored["entry_status"] == "WAIT_FOR_RETEST"
    assert stored["lifecycle_status"] == "pending_retest"
    assert stored["executable_at"] is None
    assert outcome_check["checked"] == 0
    assert snapshot["detected_ideas"] == 1
    assert snapshot["pending_retest_count"] == 1
    assert snapshot["executable_ideas"] == 0
    assert snapshot["completed_opportunities"] == 0
    assert snapshot["expectancy_per_trade"] == 0


def test_later_confirmed_retest_promotes_without_duplicate(monkeypatch, tmp_path):
    configure_db(monkeypatch, tmp_path)
    pending = idea(entry_status="WAIT_FOR_RETEST", entry_zone=(100.0, 102.0))
    confirmation_time = SIGNAL_TIME + timedelta(hours=4)
    confirmed = idea(
        candle_time=confirmation_time,
        entry_status="READY",
        entry_zone=(100.5, 102.5),
        candle_high=103.0,
        candle_low=100.5,
        candle_close=101.5,
        confirmations=["volume-backed rejection at support"],
    )

    save_trade_ideas([pending])
    save_trade_ideas([confirmed])
    save_trade_ideas([confirmed.model_copy(update={"entry_zone": (100.75, 102.75)})])

    stored = rows()
    assert len(stored) == 1
    assert stored[0]["entry_status"] == "READY"
    assert stored[0]["signal_candle_time"] == SIGNAL_TIME.isoformat()
    assert stored[0]["retest_confirmed_at"] == confirmation_time.isoformat()
    assert stored[0]["executable_at"] is not None
    assert stored[0]["entry_zone_low"] == 100.75


def test_same_impulse_candle_cannot_promote_pending_setup(monkeypatch, tmp_path):
    configure_db(monkeypatch, tmp_path)
    save_trade_ideas([idea(entry_status="WAIT_FOR_RETEST")])
    same_impulse = idea(entry_status="READY", confirmations=["rejection at support"])
    save_trade_ideas([same_impulse])
    stored = rows()
    assert len(stored) == 1
    assert stored[0]["entry_status"] == "WAIT_FOR_RETEST"
    assert stored[0]["executable_at"] is None
    assert same_impulse.entry_status == "WAIT_FOR_RETEST"


def test_channel_dedupe_uses_opportunity_identity_not_changed_prices(monkeypatch, tmp_path):
    configure_db(monkeypatch, tmp_path)
    first = idea()
    changed = idea(entry_zone=(100.2, 102.2), stop=95.8, tp1=108.2, tp2=116.2)

    mark_alert_sent(first, namespace="telegram", now=SIGNAL_TIME + timedelta(minutes=1))

    assert should_skip_alert(changed, namespace="telegram", now=SIGNAL_TIME + timedelta(minutes=2)) is True
    assert should_skip_alert(changed, namespace="telegram", now=SIGNAL_TIME + timedelta(days=2)) is True


def test_wait_for_retest_cannot_reach_auto_execution(monkeypatch, tmp_path):
    configure_db(monkeypatch, tmp_path)
    monkeypatch.setenv("EXECUTION_AUTOTRADE_ENABLED", "true")
    monkeypatch.setenv("EXECUTION_SIGNAL_WEBHOOK_URL", "https://executor.invalid/signal")
    get_settings.cache_clear()
    from app.services import execution_signals

    calls = []

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, *args, **kwargs):
            calls.append((args, kwargs))
            raise AssertionError("WAIT_FOR_RETEST must not be posted")

    monkeypatch.setattr(execution_signals.httpx, "AsyncClient", lambda **kwargs: FakeClient())
    asyncio.run(execution_signals.dispatch_trade_ideas_to_execution([idea(entry_status="WAIT_FOR_RETEST")]))
    assert calls == []


def test_existing_historical_row_remains_readable_after_migration(monkeypatch, tmp_path):
    db_path = tmp_path / "legacy.db"
    connection = sqlite3.connect(db_path)
    connection.execute(
        """
        CREATE TABLE trade_ideas (
            id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL, timeframe TEXT NOT NULL,
            exchange TEXT NOT NULL, direction TEXT NOT NULL, market_regime TEXT,
            higher_timeframe_bias TEXT, setup_score REAL, setup_grade TEXT,
            entry_zone_low REAL NOT NULL, entry_zone_high REAL NOT NULL, stop_loss REAL NOT NULL,
            take_profit_1 REAL NOT NULL, take_profit_2 REAL NOT NULL, risk_reward REAL NOT NULL,
            confidence REAL NOT NULL, reason TEXT NOT NULL, invalidation TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, status TEXT NOT NULL DEFAULT 'PENDING',
            outcome_checked_at TEXT, entry_triggered_at TEXT, closed_at TEXT,
            result TEXT NOT NULL DEFAULT 'OPEN', pnl_r_multiple REAL
        )
        """
    )
    connection.execute(
        """
        INSERT INTO trade_ideas (
            symbol,timeframe,exchange,direction,entry_zone_low,entry_zone_high,stop_loss,
            take_profit_1,take_profit_2,risk_reward,confidence,reason,invalidation
        ) VALUES ('ETHUSDT','4h','hyperliquid','LONG',100,101,95,108,115,2.5,80,'legacy','legacy invalidation')
        """
    )
    connection.commit()
    connection.close()

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    get_settings.cache_clear()
    database._INITIALIZED = False
    record = get_trade_history(1)

    assert record is not None
    validated = TradeHistoryRecord.model_validate(record)
    assert validated.symbol == "ETHUSDT"
    assert validated.entry_status is None
    assert validated.opportunity_key is None
    assert validated.strict_trend_short_eligible is None
    assert validated.strict_trigger_type is None
