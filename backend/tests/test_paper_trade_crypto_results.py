import asyncio
from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from app.config import get_settings
from app.routes import paper_trades
from app.routes.paper_trades import _refresh_crypto_paper_trades, evaluate_crypto_paper_trade
from app.utils import database
from app.utils.database import get_connection


TAKEN_AT = datetime(2026, 8, 27, 10, 0, tzinfo=UTC)


def trade(**overrides):
    base = {
        "id": 1,
        "symbol": "BTCUSDT",
        "timeframe": "1h",
        "exchange": "hyperliquid",
        "direction": "Long",
        "entry_price": 100.0,
        "stop_loss": 95.0,
        "take_profit_1": 110.0,
        "take_profit_2": 120.0,
        "risk_reward": 4.0,
        "taken_at": TAKEN_AT,
        "created_at": TAKEN_AT,
    }
    return {**base, **overrides}


def candles(*rows):
    return pd.DataFrame([
        {
            "timestamp": TAKEN_AT + timedelta(hours=index + 1),
            "open": close,
            "high": high,
            "low": low,
            "close": close,
            "volume": 1.0,
        }
        for index, (high, low, close) in enumerate(rows)
    ])


def test_saved_crypto_trade_tracks_tp1_then_tp2_and_latest_price():
    outcome = evaluate_crypto_paper_trade(
        trade(),
        candles((104.0, 99.0, 100.0), (111.0, 101.0, 108.0), (121.0, 107.0, 118.0)),
    )

    assert outcome["lifecycle_status"] == "TP2_HIT"
    assert outcome["status"] == "closed"
    assert outcome["result"] == "closed"  # No funding evidence in the pure evaluator.
    assert outcome["result_r"] is None
    assert outcome["gross_r_multiple"] == 3.0  # Half at 2R, half at 4R.
    assert outcome["tp1_hit_at"] is not None
    assert outcome["tp2_hit_at"] is not None
    assert outcome["latest_price"] == 118.0


def test_saved_crypto_trade_tracks_stop_loss_for_user_history():
    outcome = evaluate_crypto_paper_trade(trade(), candles((104.0, 99.0, 100.0), (104.0, 94.0, 96.0)))

    assert outcome["lifecycle_status"] == "STOPPED"
    assert outcome["status"] == "closed"
    assert outcome["result"] == "closed"
    assert outcome["result_r"] is None
    assert outcome["gross_r_multiple"] == -1.0
    assert outcome["stopped_at"] is not None


def test_saved_short_trade_reports_tp1_running_and_live_price():
    outcome = evaluate_crypto_paper_trade(
        trade(direction="Short", stop_loss=105.0, take_profit_1=90.0, take_profit_2=80.0),
        candles((101.0, 96.0, 100.0), (99.0, 89.0, 91.0)),
        as_of=TAKEN_AT+timedelta(hours=3),
    )

    assert outcome["lifecycle_status"] == "TP1_HIT_TP2_RUNNING"
    assert outcome["status"] == "tp_hit"
    assert outcome["result"] == "open"
    assert outcome["latest_price"] == 91.0


@pytest.fixture
def paper_trade_db(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'paper-trades.db'}")
    get_settings.cache_clear()
    database._INITIALIZED = False
    database.init_db()
    yield
    database._INITIALIZED = False
    get_settings.cache_clear()


def test_crypto_refresh_persists_resolved_status(paper_trade_db, monkeypatch):
    with get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO paper_trades (
                user_id, signal_id, symbol, timeframe, exchange, direction,
                entry_price, stop_loss, take_profit_1, take_profit_2, size,
                risk_reward, status, result, taken_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'taken', 'open', ?, ?)
            """,
            (
                "user-1", "crypto-signal-123", "BTCUSDT", "1h", "hyperliquid", "Long",
                100.0, 95.0, 110.0, 120.0, 1.0, 4.0,
                TAKEN_AT.isoformat(), TAKEN_AT.isoformat(),
            ),
        )
        row = dict(connection.execute("SELECT * FROM paper_trades WHERE id = ?", (cursor.lastrowid,)).fetchone())

    async def fake_candles(*_args):
        return candles((104.0, 99.0, 100.0), (104.0, 94.0, 96.0))

    async def fake_funding(*_args):
        return [dict(coin="BTC", time=int((TAKEN_AT+timedelta(hours=i)).timestamp()*1000), fundingRate="0") for i in range(1,3)]

    monkeypatch.setattr(paper_trades, "get_candles_cached", fake_candles)
    from app.services import tracking_funding
    monkeypatch.setattr(tracking_funding, "fetch_rates", fake_funding)
    refreshed = asyncio.run(_refresh_crypto_paper_trades([row]))

    assert refreshed[0]["lifecycle_status"] == "STOPPED"
    with get_connection() as connection:
        stored = connection.execute("SELECT status, result, pnl FROM paper_trades WHERE id = ?", (row["id"],)).fetchone()
    assert dict(stored) == {"status": "closed", "result": "loss", "pnl": -1.0195}
    assert refreshed[0]["result_r"] == pytest.approx(-1.0195)
    with get_connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM tracking_corrections WHERE asset='PAPER'").fetchone()[0] == 1


def test_paper_entry_and_target_in_same_candle_remain_ambiguous():
    outcome = evaluate_crypto_paper_trade(trade(), candles((111,99,108)))
    assert outcome['lifecycle_status'] == 'AMBIGUOUS'
    assert outcome['result_r'] is None
    assert outcome['activated_at'] is not None


def test_paper_save_does_not_establish_a_price_fill():
    outcome = evaluate_crypto_paper_trade(trade(), candles((104,101,103)), as_of=TAKEN_AT+timedelta(hours=2))
    assert outcome['activated_at'] is None
    assert outcome['result_r'] is None
