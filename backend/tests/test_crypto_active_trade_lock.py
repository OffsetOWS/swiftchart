from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from types import SimpleNamespace

import pandas as pd

from app.config import get_settings
from app.models.schemas import TradeIdea
from app.services import scanner
from app.services.strategy_v2_analytics import strategy_v2_performance_report
from app.services.trade_history import active_crypto_trade_for_symbol, save_trade_ideas
from app.strategy.decision_engine import evaluate_strategy_decision
from app.utils import database


START = datetime.now(UTC).replace(minute=0, second=0, microsecond=0) - timedelta(hours=4)


def idea(
    *,
    symbol: str = "BTCUSDT",
    candle_time: datetime = START,
    direction: str = "Long",
    regime: str = "RANGE_BOUND",
    entry_status: str = "READY",
) -> TradeIdea:
    short = direction == "Short"
    candidate = TradeIdea(
        symbol=symbol,
        timeframe="4h",
        exchange="hyperliquid",
        direction=direction,
        market_regime=regime,
        entry_zone=(100.0, 102.0),
        stop_loss=107.0 if short else 95.0,
        take_profit_1=92.0 if short else 110.0,
        take_profit_2=84.0 if short else 118.0,
        risk_reward_ratio=2.5,
        reason="Crypto active trade lock regression test.",
        confidence_score=96,
        setup_score=96,
        invalid_condition="Invalid beyond stop.",
        entry_status=entry_status,
        signal_candle_time=candle_time,
        signal_candle_high=103.0,
        signal_candle_low=100.5,
        signal_candle_close=101.5,
        regime_confidence_score=99,
    )
    evaluate_strategy_decision(candidate)
    return candidate


def configure_db(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'swiftchart.db'}")
    monkeypatch.setenv("ALERT_DEDUPE_STATE_PATH", str(tmp_path / "dedupe.json"))
    get_settings.cache_clear()
    database._INITIALIZED = False
    scanner._scan_cache.clear()
    scanner._scan_offsets.clear()


def rows() -> list[dict]:
    with database.get_connection() as connection:
        return [dict(row) for row in connection.execute("SELECT * FROM trade_ideas ORDER BY id")]


def activate_trade(candidate: TradeIdea | None = None) -> int:
    trade = candidate or idea()
    assert trade.strategy_decision == "TRADE"
    saved = save_trade_ideas([trade])
    assert len(saved) == 1
    return saved[0]


def test_active_trade_without_tp1_or_sl_skips_symbol_before_evaluation(monkeypatch, tmp_path):
    configure_db(monkeypatch, tmp_path)
    trade_id = activate_trade()

    async def markets(_exchange):
        return [{"exchange": "hyperliquid", "symbol": "BTC-USDT", "exchange_symbol": "BTCUSDT"}]

    async def fail_prefilter(*_args, **_kwargs):
        raise AssertionError("A locked symbol must be skipped before candle fetching or evaluation")

    async def no_dispatch(ideas):
        assert ideas == []

    monkeypatch.setattr(scanner, "discover_all_scan_markets", markets)
    monkeypatch.setattr(scanner, "selected_exchanges", lambda _exchange: ["hyperliquid"])
    monkeypatch.setattr(scanner, "_prefilter_market", fail_prefilter)
    monkeypatch.setattr(scanner, "dispatch_trade_ideas_to_execution", no_dispatch)

    result = asyncio.run(scanner.run_scan(exchange="hyperliquid", timeframe="4h", force=True))

    assert result["ideas"] == []
    assert result["scan_stats"]["active_trade_skips"] == 1
    diagnostic = result["diagnostics"][0]
    assert diagnostic["decision"] == "SKIPPED_ACTIVE_TRADE"
    assert diagnostic["existing_trade_id"] == trade_id
    assert diagnostic["existing_signal_id"].startswith("swiftchart-")
    assert "neither TP1 nor SL" in diagnostic["reason"]


def test_repeated_locked_scans_do_not_inflate_audit_then_tp1_allows_next_trade(monkeypatch, tmp_path):
    configure_db(monkeypatch, tmp_path)
    first_id = activate_trade()

    async def markets(_exchange):
        return [{"exchange": "hyperliquid", "symbol": "BTCUSDT", "exchange_symbol": "BTCUSDT"}]

    async def fail_prefilter(*_args, **_kwargs):
        raise AssertionError("Locked scans must stop before candle fetching and strategy evaluation")

    dispatched: list[list[TradeIdea]] = []

    async def capture_dispatch(ideas):
        dispatched.append(list(ideas))

    monkeypatch.setattr(scanner, "discover_all_scan_markets", markets)
    monkeypatch.setattr(scanner, "selected_exchanges", lambda _exchange: ["hyperliquid"])
    monkeypatch.setattr(scanner, "_prefilter_market", fail_prefilter)
    monkeypatch.setattr(scanner, "dispatch_trade_ideas_to_execution", capture_dispatch)

    for _ in range(3):
        result = asyncio.run(scanner.run_scan(exchange="hyperliquid", timeframe="4h", force=True))
        assert result["ideas"] == []
        assert result["diagnostics"][0]["decision"] == "SKIPPED_ACTIVE_TRADE"

    before_unlock = strategy_v2_performance_report()
    assert len(rows()) == 1
    assert before_unlock["overall"]["detected_opportunities"] == 1
    assert before_unlock["overall"]["actionable_opportunities"] == 1
    assert sum(group["actionable_opportunities"] for group in before_unlock["breakdown"]) == 1
    assert all(group.get("strategy_decision") != "SKIPPED_ACTIVE_TRADE" for group in before_unlock["breakdown"])

    with database.get_connection() as connection:
        connection.execute(
            "UPDATE trade_ideas SET status='TP1_HIT', tp1_hit_at=?, result='OPEN' WHERE id=?",
            (datetime.now(UTC).isoformat(), first_id),
        )

    next_trade = idea(candle_time=START + timedelta(hours=4))
    candles = pd.DataFrame(
        {
            "open": [100.0 + index * 0.1 for index in range(100)],
            "high": [101.0 + index * 0.1 for index in range(100)],
            "low": [99.0 + index * 0.1 for index in range(100)],
            "close": [100.5 + index * 0.1 for index in range(100)],
            "volume": [10_000.0 for _ in range(100)],
        }
    )

    async def eligible_prefilter(_market, _timeframe, _semaphore, _stats):
        return scanner.Candidate("hyperliquid", "BTCUSDT", "BTCUSDT", candles, 1_000_000, 1.0)

    monkeypatch.setattr(scanner, "_prefilter_market", eligible_prefilter)
    monkeypatch.setattr(scanner, "higher_timeframes_for", lambda _timeframe: [])
    monkeypatch.setattr(
        scanner,
        "analyze_dataframe",
        lambda *_args, **_kwargs: SimpleNamespace(trade_ideas=[next_trade], rejected_signals=[]),
    )

    unlocked_result = asyncio.run(scanner.run_scan(exchange="hyperliquid", timeframe="4h", force=True))
    after_unlock = strategy_v2_performance_report()

    assert unlocked_result["ideas"] == [next_trade]
    assert unlocked_result["diagnostics"] == []
    assert len(rows()) == 2
    assert after_unlock["overall"]["detected_opportunities"] == 2
    assert after_unlock["overall"]["actionable_opportunities"] == 2
    assert sum(group["actionable_opportunities"] for group in after_unlock["breakdown"]) == 2
    assert dispatched[-1] == [next_trade]


def test_tp1_hit_unlocks_symbol(monkeypatch, tmp_path):
    configure_db(monkeypatch, tmp_path)
    trade_id = activate_trade()
    with database.get_connection() as connection:
        connection.execute(
            "UPDATE trade_ideas SET status='TP1_HIT', tp1_hit_at=?, result='OPEN' WHERE id=?",
            (datetime.now(UTC).isoformat(), trade_id),
        )

    assert active_crypto_trade_for_symbol("BTC/USDT") is None


def test_sl_hit_unlocks_symbol(monkeypatch, tmp_path):
    configure_db(monkeypatch, tmp_path)
    trade_id = activate_trade()
    with database.get_connection() as connection:
        connection.execute(
            "UPDATE trade_ideas SET status='SL_HIT', sl_hit_at=?, closed_at=?, result='LOSS' WHERE id=?",
            (datetime.now(UTC).isoformat(), datetime.now(UTC).isoformat(), trade_id),
        )

    assert active_crypto_trade_for_symbol("btcusdc") is None


def test_tp1_hit_with_tp2_running_allows_new_trade_and_keeps_runner(monkeypatch, tmp_path):
    configure_db(monkeypatch, tmp_path)
    first_id = activate_trade()
    tp1_at = datetime.now(UTC).isoformat()
    with database.get_connection() as connection:
        connection.execute(
            "UPDATE trade_ideas SET status='TP1_HIT', tp1_hit_at=?, result='OPEN' WHERE id=?",
            (tp1_at, first_id),
        )

    second_id = activate_trade(idea(candle_time=START + timedelta(hours=4)))
    stored = rows()

    assert second_id != first_id
    assert len(stored) == 2
    assert stored[0]["status"] == "TP1_HIT"
    assert stored[0]["tp1_hit_at"] == tp1_at
    assert stored[0]["tp2_hit_at"] is None
    assert stored[0]["closed_at"] is None
    assert active_crypto_trade_for_symbol("BTCUSDT")["id"] == second_id


def test_non_trade_decisions_do_not_lock_symbol(monkeypatch, tmp_path):
    configure_db(monkeypatch, tmp_path)
    waiting = idea(symbol="BTCUSDT", entry_status="WAIT_FOR_RETEST")
    shadow = idea(symbol="ETHUSDT", regime="TRENDING_UP")
    no_trade = idea(symbol="SOLUSDT", regime="BREAKOUT")

    assert waiting.strategy_decision == "WAIT_FOR_RETEST"
    assert shadow.strategy_decision == "SHADOW"
    assert no_trade.strategy_decision == "NO_TRADE"
    save_trade_ideas([waiting, shadow, no_trade])

    assert active_crypto_trade_for_symbol("BTCUSDT") is None
    assert active_crypto_trade_for_symbol("ETHUSDT") is None
    assert active_crypto_trade_for_symbol("SOLUSDT") is None


def test_expired_pre_entry_trade_does_not_lock_symbol(monkeypatch, tmp_path):
    configure_db(monkeypatch, tmp_path)
    trade_id = activate_trade()
    with database.get_connection() as connection:
        connection.execute(
            "UPDATE trade_ideas SET status='EXPIRED', expired_at=?, closed_at=?, result='NO_ENTRY' WHERE id=?",
            (datetime.now(UTC).isoformat(), datetime.now(UTC).isoformat(), trade_id),
        )

    assert active_crypto_trade_for_symbol("BTCUSDT") is None


def test_database_lock_survives_scanner_service_restart(monkeypatch, tmp_path):
    configure_db(monkeypatch, tmp_path)
    trade_id = activate_trade()

    database._INITIALIZED = False
    scanner._scan_cache.clear()
    scanner._scan_offsets.clear()

    active = active_crypto_trade_for_symbol("BTC-PERP")
    assert active is not None
    assert active["id"] == trade_id


def test_concurrent_scanner_workers_allow_only_one_pre_tp1_trade(monkeypatch, tmp_path):
    configure_db(monkeypatch, tmp_path)
    barrier = Barrier(2)

    def persist(candidate: TradeIdea) -> tuple[list[int], list[dict]]:
        diagnostics: list[dict] = []
        barrier.wait()
        saved = save_trade_ideas([candidate], diagnostics=diagnostics)
        return saved, diagnostics

    candidates = [
        idea(candle_time=START),
        idea(candle_time=START + timedelta(hours=4)),
    ]
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(persist, candidates))

    stored = rows()
    active = [row for row in stored if row["strategy_decision"] == "TRADE" and row["tp1_hit_at"] is None]
    diagnostics = [diagnostic for _, result_diagnostics in results for diagnostic in result_diagnostics]

    assert len(stored) == 1
    assert len(active) == 1
    assert sorted(len(saved) for saved, _ in results) == [0, 1]
    assert len(diagnostics) == 1
    assert diagnostics[0]["decision"] == "SKIPPED_ACTIVE_TRADE"
    assert diagnostics[0]["existing_trade_id"] == active[0]["id"]
