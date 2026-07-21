from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import sqlite3

from app.config import get_settings
from app.models.schemas import TradeIdea
from app.services import execution_signals
from app.services.strategy_v2_analytics import strategy_edge_registry_report, strategy_v2_performance_report
from app.services.strategy_experiments import strict_trend_short_shadow_report
from app.services.trade_history import save_trade_ideas
from app.strategy.decision_engine import evaluate_strategy_decision
from app.utils import database
from bot.alerts import is_limit_order_alertable


START = datetime.now(UTC).replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)


def idea(
    regime: str,
    direction: str,
    *,
    candle_time: datetime = START,
    entry_status: str = "READY",
    confirmations: list[str] | None = None,
    strict: bool | None = None,
    strategy_version: str | None = None,
) -> TradeIdea:
    long = direction == "Long"
    return TradeIdea(
        symbol="BTCUSDT",
        timeframe="4h",
        exchange="hyperliquid",
        direction=direction,
        market_regime=regime,
        entry_zone=(100.0, 102.0),
        stop_loss=95.0 if long else 107.0,
        take_profit_1=110.0 if long else 92.0,
        take_profit_2=118.0 if long else 84.0,
        risk_reward_ratio=2.5,
        reason="V2 strategy decision test.",
        confidence_score=96,
        setup_score=96,
        invalid_condition="Invalid beyond stop.",
        entry_status=entry_status,
        signal_candle_time=candle_time,
        signal_candle_high=103.0,
        signal_candle_low=100.5,
        signal_candle_close=101.5,
        reversal_confirmations=confirmations or [],
        regime_confidence_score=99,
        strategy_version=strategy_version,
        production_rule_accepted=True if regime == "TRENDING_DOWN" and direction == "Short" else None,
        strict_trend_short_eligible=strict,
        strict_trigger_type="resistance_rejection" if strict else None,
        strict_confirmation_type="htf_bearish_alignment" if strict is not None else None,
        strict_trigger_candle_time=candle_time if strict else None,
        strict_trigger_candle_completed=True if strict is not None else None,
    )


def configure_db(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'swiftchart.db'}")
    monkeypatch.setenv("ALERT_DEDUPE_STATE_PATH", str(tmp_path / "dedupe.json"))
    get_settings.cache_clear()
    database._INITIALIZED = False


def stored_rows():
    with database.get_connection() as connection:
        return [dict(row) for row in connection.execute("SELECT * FROM trade_ideas ORDER BY id")]


def test_initial_registry_statuses_are_explicit_and_not_auto_activated():
    report = strategy_edge_registry_report()
    statuses = {entry["strategy_family"]: entry["status"] for entry in report["entries"]}
    assert statuses == {
        "range_mean_reversion": "VALIDATED",
        "trend_continuation": "EXPERIMENTAL",
        "breakout": "DISABLED",
        "regime_transition": "UNVALIDATED",
    }
    assert "never auto-activates" in report["activation_policy"]


def test_validated_range_can_reach_trade_and_separates_entry_quality():
    trade = idea("RANGE_BOUND", "Long")
    decision = evaluate_strategy_decision(trade)
    assert decision == "TRADE"
    assert trade.edge_status == "VALIDATED"
    assert trade.regime_confidence == 99
    assert trade.entry_quality_status == "PASS"
    assert trade.entry_quality_score is None
    assert trade.outcome_tracking_mode == "PRODUCTION"


def test_regime_confidence_does_not_activate_disabled_or_unvalidated_edges():
    breakout = idea("BREAKOUT", "Long")
    transition = idea("TRANSITION_TO_BULLISH", "Long")
    assert evaluate_strategy_decision(breakout) == "NO_TRADE"
    assert evaluate_strategy_decision(transition) == "NO_TRADE"
    assert breakout.regime_confidence == transition.regime_confidence == 99
    assert breakout.entry_quality_status == transition.entry_quality_status == "PASS"
    assert breakout.edge_status == "DISABLED"
    assert transition.edge_status == "UNVALIDATED"


def test_unsupported_regime_returns_no_trade():
    unsupported = idea("CHOP", "Long")
    assert evaluate_strategy_decision(unsupported) == "NO_TRADE"
    assert unsupported.edge_status is None
    assert unsupported.opportunity_key is None


def test_strict_trend_short_remains_shadow_only_and_non_actionable(monkeypatch, tmp_path):
    configure_db(monkeypatch, tmp_path)
    trend_short = idea("TRENDING_DOWN", "Short", strict=True)
    assert evaluate_strategy_decision(trend_short) == "SHADOW"
    assert trend_short.strict_trend_short_eligible is True
    assert trend_short.production_rule_accepted is True
    assert trend_short.outcome_tracking_mode == "SHADOW"
    assert is_limit_order_alertable(trend_short) is False
    save_trade_ideas([trend_short])
    row = stored_rows()[0]
    assert row["lifecycle_status"] == "shadow"
    assert row["executable_at"] is None
    assert row["strict_trend_short_eligible"] == 1


def test_strict_trend_short_report_keeps_v2_reference_cohort(monkeypatch, tmp_path):
    configure_db(monkeypatch, tmp_path)
    reference = [
        idea("TRENDING_DOWN", "Short", strict=True),
        idea("TRENDING_DOWN", "Short", candle_time=START + timedelta(hours=4), strict=False),
    ]
    for candidate in reference:
        assert evaluate_strategy_decision(candidate) == "SHADOW"
    save_trade_ideas(reference)
    with database.get_connection() as connection:
        rows = connection.execute("SELECT id, strict_trend_short_eligible FROM trade_ideas ORDER BY id").fetchall()
        connection.execute(
            "UPDATE trade_ideas SET status='TP2_HIT', result='WIN', pnl_r_multiple=2 WHERE id=?",
            (rows[0]["id"],),
        )
        connection.execute(
            "UPDATE trade_ideas SET status='SL_HIT', result='LOSS', pnl_r_multiple=-1 WHERE id=?",
            (rows[1]["id"],),
        )
    report = strict_trend_short_shadow_report()
    assert report["current_production_rule"]["unique_opportunities"] == 2
    assert report["current_production_rule"]["completed_outcomes"] == 2
    assert report["experimental_strict_rule"]["unique_opportunities"] == 1
    assert report["experimental_strict_rule"]["expectancy_r"] == 2.0


def test_disabled_breakout_and_transition_cannot_reach_telegram_or_execution(monkeypatch, tmp_path):
    configure_db(monkeypatch, tmp_path)
    monkeypatch.setenv("EXECUTION_AUTOTRADE_ENABLED", "true")
    monkeypatch.setenv("EXECUTION_SIGNAL_WEBHOOK_URL", "https://executor.invalid/signal")
    get_settings.cache_clear()
    calls = []

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, *args, **kwargs):
            calls.append((args, kwargs))
            raise AssertionError("Non-actionable V2 decisions must not be posted")

    monkeypatch.setattr(execution_signals.httpx, "AsyncClient", lambda **kwargs: FakeClient())
    blocked = [idea("BREAKOUT", "Long"), idea("TRANSITION_TO_BEARISH", "Short")]
    for candidate in blocked:
        evaluate_strategy_decision(candidate)
        assert is_limit_order_alertable(candidate) is False
    asyncio.run(execution_signals.dispatch_trade_ideas_to_execution(blocked))
    save_trade_ideas(blocked)
    assert calls == []
    assert all(row["strategy_decision"] == "NO_TRADE" for row in stored_rows())
    assert all(row["executable_at"] is None for row in stored_rows())


def test_disabled_breakout_is_rejected_by_full_telegram_dispatch_boundary(monkeypatch, tmp_path):
    configure_db(monkeypatch, tmp_path)
    from bot import alerts

    breakout = idea("BREAKOUT", "Long")
    assert evaluate_strategy_decision(breakout) == "NO_TRADE"
    sent = []
    saved = []

    class FakeBot:
        async def send_message(self, **kwargs):
            sent.append(kwargs)

    async def fake_scan(*args, **kwargs):
        return [breakout], "hyperliquid", {
            "symbols_scanned": 1,
            "valid_ideas_found": 1,
            "rejection_reasons": {},
            "btc_context": None,
        }

    monkeypatch.setattr(alerts, "get_subscribers", lambda: ["chat"])
    monkeypatch.setattr(alerts, "scan_top_ideas", fake_scan)
    monkeypatch.setattr(alerts, "save_signal", lambda *args, **kwargs: saved.append((args, kwargs)))
    monkeypatch.setattr(alerts, "alert_timeframes_for_run", lambda: (["4h"], []))
    result = asyncio.run(alerts.run_alert_scan(FakeBot()))
    assert result["eligible"] == 0
    assert result["sent"] == 0
    assert sent == []
    assert saved == []


def test_validated_wait_for_retest_promotes_without_duplicate(monkeypatch, tmp_path):
    configure_db(monkeypatch, tmp_path)
    pending = idea("RANGE_BOUND", "Long", entry_status="WAIT_FOR_RETEST")
    assert evaluate_strategy_decision(pending) == "WAIT_FOR_RETEST"
    save_trade_ideas([pending])

    confirmed = idea(
        "RANGE_BOUND",
        "Long",
        candle_time=START + timedelta(minutes=30),
        confirmations=["rejection at support"],
    )
    assert evaluate_strategy_decision(confirmed) == "TRADE"
    save_trade_ideas([confirmed])

    rows = stored_rows()
    assert len(rows) == 1
    assert rows[0]["strategy_decision"] == "TRADE"
    assert rows[0]["entry_status"] == "READY"
    assert rows[0]["retest_confirmed_at"] is not None
    assert rows[0]["executable_at"] is not None


def test_experimental_trend_retest_promotes_to_shadow_not_execution(monkeypatch, tmp_path):
    configure_db(monkeypatch, tmp_path)
    pending = idea("TRENDING_DOWN", "Short", entry_status="WAIT_FOR_RETEST", strict=False)
    assert evaluate_strategy_decision(pending) == "WAIT_FOR_RETEST"
    save_trade_ideas([pending])

    confirmed = idea(
        "TRENDING_DOWN",
        "Short",
        candle_time=START + timedelta(minutes=30),
        confirmations=["rejection at resistance"],
        strict=True,
    ).model_copy(update={"signal_candle_close": 100.5})
    assert evaluate_strategy_decision(confirmed) == "SHADOW"
    save_trade_ideas([confirmed])

    rows = stored_rows()
    assert len(rows) == 1
    assert rows[0]["entry_status"] == "READY"
    assert rows[0]["strategy_decision"] == "SHADOW"
    assert rows[0]["outcome_tracking_mode"] == "SHADOW"
    assert rows[0]["executable_at"] is None
    assert rows[0]["retest_confirmed_at"] is not None


def test_disabled_strategy_cannot_promote_from_pending(monkeypatch, tmp_path):
    configure_db(monkeypatch, tmp_path)
    pending = idea("BREAKOUT", "Long", entry_status="WAIT_FOR_RETEST")
    confirmed = idea(
        "BREAKOUT",
        "Long",
        candle_time=START + timedelta(minutes=30),
        confirmations=["rejection at support"],
    )
    assert evaluate_strategy_decision(pending) == "NO_TRADE"
    assert evaluate_strategy_decision(confirmed) == "NO_TRADE"
    save_trade_ideas([pending, confirmed])
    rows = stored_rows()
    assert len(rows) == 2
    assert all(row["strategy_decision"] == "NO_TRADE" for row in rows)
    assert all(row["executable_at"] is None for row in rows)
    assert all(row["retest_confirmed_at"] is None for row in rows)


def test_strategy_versions_remain_canonically_and_analytically_separate(monkeypatch, tmp_path):
    configure_db(monkeypatch, tmp_path)
    v1 = idea("RANGE_BOUND", "Long")
    v2 = idea("RANGE_BOUND", "Long", strategy_version="v2")
    assert evaluate_strategy_decision(v1) == "TRADE"
    assert evaluate_strategy_decision(v2) == "NO_TRADE"
    assert v1.opportunity_key != v2.opportunity_key
    save_trade_ideas([v1, v2])
    with database.get_connection() as connection:
        connection.execute(
            "UPDATE trade_ideas SET status='TP2_HIT', result='WIN', pnl_r_multiple=2.5 WHERE strategy_version='v1'"
        )
    report = strategy_v2_performance_report()
    assert report["overall"]["detected_opportunities"] == 2
    by_version = {row["strategy_version"]: row for row in report["breakdown"]}
    assert set(by_version) == {"v1", "v2"}
    assert by_version["v1"]["completed_opportunities"] == 1
    assert by_version["v1"]["expectancy_r"] == 2.5
    assert by_version["v2"]["no_trade_decisions"] == 1


def test_migration_leaves_existing_forex_table_and_rows_untouched(monkeypatch, tmp_path):
    db_path = tmp_path / "shared.db"
    connection = sqlite3.connect(db_path)
    connection.execute("CREATE TABLE forex_signals (id INTEGER PRIMARY KEY, pair TEXT, status TEXT)")
    connection.execute("INSERT INTO forex_signals (id, pair, status) VALUES (1, 'EURUSD', 'active')")
    connection.commit()
    connection.close()

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    get_settings.cache_clear()
    database._INITIALIZED = False
    database.init_db()

    connection = sqlite3.connect(db_path)
    assert connection.execute("SELECT id, pair, status FROM forex_signals").fetchall() == [(1, "EURUSD", "active")]
    assert [row[1] for row in connection.execute("PRAGMA table_info(forex_signals)")] == ["id", "pair", "status"]
    connection.close()
