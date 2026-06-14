from datetime import UTC, datetime, timedelta

import pandas as pd

from app.services.trade_history import evaluate_trade


def row() -> dict:
    return {
        "id": 1,
        "symbol": "BTCUSDT",
        "timeframe": "4h",
        "exchange": "hyperliquid",
        "direction": "LONG",
        "entry_zone_low": 100.0,
        "entry_zone_high": 101.0,
        "stop_loss": 95.0,
        "take_profit_1": 105.0,
        "take_profit_2": 110.0,
        "risk_reward": 2.0,
        "created_at": datetime(2026, 5, 1, 0, 0, tzinfo=UTC).isoformat(),
        "status": "OPEN",
        "result": "OPEN",
    }


def candles(*bars: tuple[float, float]) -> pd.DataFrame:
    start = datetime(2026, 5, 1, 4, 0, tzinfo=UTC)
    return pd.DataFrame(
        [
            {
                "timestamp": start + timedelta(hours=4 * index),
                "open": 100.0,
                "high": high,
                "low": low,
                "close": 100.0,
                "volume": 1_000.0,
            }
            for index, (high, low) in enumerate(bars)
        ]
    )


def test_lifecycle_continues_after_tp1_until_tp2():
    outcome = evaluate_trade(row(), candles((106.0, 100.0), (111.0, 104.0)), expiry_bars=12)

    assert outcome["status"] == "TP2_HIT"
    assert outcome["result"] == "WIN"
    assert outcome["tp1_hit_at"] is not None
    assert outcome["tp2_hit_at"] is not None
    assert outcome["closed_at"] == outcome["tp2_hit_at"]
    assert outcome["candles_to_resolution"] == 2


def test_lifecycle_tracks_sl_after_tp1_without_break_even():
    outcome = evaluate_trade(row(), candles((106.0, 100.0), (104.0, 94.0)), expiry_bars=12, move_stop_to_entry_after_tp1=False)

    assert outcome["status"] == "CLOSED_AFTER_TP1"
    assert outcome["result"] == "PARTIAL_LOSS"
    assert outcome["tp1_hit_at"] is not None
    assert outcome["sl_hit_at"] is not None
    assert outcome["closed_at"] == outcome["sl_hit_at"]


def test_lifecycle_tracks_expiry_after_tp1():
    outcome = evaluate_trade(row(), candles((106.0, 100.0), (104.0, 99.0), (103.0, 99.0)), expiry_bars=3)

    assert outcome["status"] == "PARTIAL_WIN"
    assert outcome["result"] == "PARTIAL_WIN"
    assert outcome["tp1_hit_at"] is not None
    assert outcome["expired_at"] is not None
    assert outcome["candles_to_resolution"] == 3
    assert "EXPIRED_AFTER_TP1" in outcome["lifecycle_events"]


def test_lifecycle_can_move_stop_to_break_even_after_tp1():
    outcome = evaluate_trade(row(), candles((106.0, 100.0), (104.0, 100.5)), expiry_bars=12, move_stop_to_entry_after_tp1=True)

    assert outcome["status"] == "BREAK_EVEN"
    assert outcome["result"] == "BREAK_EVEN"
    assert outcome["tp1_hit_at"] is not None
    assert outcome["sl_hit_at"] is not None
