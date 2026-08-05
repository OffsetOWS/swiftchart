import json
import sqlite3

from app.routes.paper_trades import _enrich_paper_trade


def test_saved_forex_trade_includes_persisted_lifecycle_result():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute(
        """
        CREATE TABLE paper_trades (
            id INTEGER, notes TEXT, exchange TEXT, symbol TEXT, timeframe TEXT,
            direction TEXT, entry_price REAL, stop_loss REAL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE forex_signals (
            id INTEGER, public_id TEXT, symbol TEXT, pair TEXT, timeframe TEXT,
            execution_timeframe TEXT, direction TEXT, entry_price REAL, entry REAL,
            entry_low REAL, entry_high REAL, stop_loss REAL, status TEXT,
            risk_reward_1 REAL, risk_reward_2 REAL, rr REAL, latest_price REAL,
            last_market_price REAL, latest_price_at TEXT, last_price_updated_at TEXT,
            activated_at TEXT, tp1_hit_at TEXT, tp2_hit_at TEXT, stopped_at TEXT,
            closed_at TEXT, expires_at TEXT, market_session TEXT, session TEXT,
            setup_reason TEXT, reason TEXT, created_at TEXT
        )
        """
    )
    connection.execute(
        "INSERT INTO paper_trades VALUES (1, ?, 'forex', 'EURUSD', '4h', 'long', 1.1, 1.09)",
        (json.dumps({"source_signal_id": "fx-result-1"}),),
    )
    connection.execute(
        """
        INSERT INTO forex_signals VALUES (
            1, 'fx-result-1', 'EURUSD', 'EUR/USD', '4H', '4h', 'BUY', 1.1, 1.1,
            1.099, 1.101, 1.09, 'TP2_HIT', 1.2, 2.4, 2.4, 1.124, 1.124,
            '2026-08-05 10:00:00', NULL, '2026-08-05 08:00:00',
            '2026-08-05 09:00:00', '2026-08-05 10:00:00', NULL,
            '2026-08-05 10:00:00', '2026-08-06 08:00:00', 'London', NULL,
            'Bullish continuation confirmed.', NULL, '2026-08-05 07:00:00'
        )
        """
    )

    trade = connection.execute("SELECT * FROM paper_trades WHERE id = 1").fetchone()
    enriched = _enrich_paper_trade(connection, trade)

    assert enriched["source_signal_id"] == "fx-result-1"
    assert enriched["lifecycle_status"] == "TP2_HIT"
    assert enriched["lifecycle_label"] == "TP2 Hit"
    assert enriched["lifecycle_result"] == "win"
    assert enriched["result_r"] == 2.4
    assert enriched["latest_price"] == 1.124


def test_legacy_saved_trade_matches_signal_by_plan_fields():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """
        CREATE TABLE paper_trades (
            id INTEGER, notes TEXT, exchange TEXT, symbol TEXT, timeframe TEXT,
            direction TEXT, entry_price REAL, stop_loss REAL
        );
        CREATE TABLE forex_signals (
            id INTEGER, public_id TEXT, symbol TEXT, pair TEXT, timeframe TEXT,
            execution_timeframe TEXT, direction TEXT, entry_price REAL, entry REAL,
            entry_low REAL, entry_high REAL, stop_loss REAL, status TEXT,
            risk_reward_1 REAL, risk_reward_2 REAL, rr REAL, latest_price REAL,
            last_market_price REAL, latest_price_at TEXT, last_price_updated_at TEXT,
            activated_at TEXT, tp1_hit_at TEXT, tp2_hit_at TEXT, stopped_at TEXT,
            closed_at TEXT, expires_at TEXT, market_session TEXT, session TEXT,
            setup_reason TEXT, reason TEXT, created_at TEXT
        );
        INSERT INTO paper_trades VALUES (1, '{}', 'forex', 'USDJPY', '30m', 'long', 158.27, 157.96);
        INSERT INTO forex_signals VALUES (
            1, 'legacy-match', 'USDJPY', 'USD/JPY', '30M', '30m', 'BUY', 158.27, 158.27,
            158.24, 158.30, 157.96, 'STOPPED', 1.1, 2.3, 2.3, 157.96, 157.96,
            NULL, NULL, NULL, NULL, NULL, '2026-08-05 10:00:00',
            '2026-08-05 10:00:00', NULL, 'Tokyo', NULL, 'Range reclaim failed.', NULL,
            '2026-08-05 07:00:00'
        );
        """
    )

    trade = connection.execute("SELECT * FROM paper_trades WHERE id = 1").fetchone()
    enriched = _enrich_paper_trade(connection, trade)

    assert enriched["source_signal_id"] == "legacy-match"
    assert enriched["lifecycle_label"] == "Stop Loss"
    assert enriched["result_r"] == -1.0
