from datetime import UTC, datetime, timedelta

from app.services.tracking_intrabar import intrabar_candles_needed, refine_ambiguous
from app.services.tracking_ledger import guard_crypto_transition
from app.services.tracking_rules import replay_crypto

START = datetime(2026, 5, 1, 4, 0, tzinfo=UTC)
AS_OF = datetime(2026, 5, 4, 0, 0, tzinfo=UTC)


def row() -> dict:
    return {
        "id": 1, "symbol": "BTCUSDT", "timeframe": "4h", "exchange": "hyperliquid", "direction": "LONG",
        "entry_zone_low": 100.0, "entry_zone_high": 101.0, "stop_loss": 95.0,
        "take_profit_1": 105.0, "take_profit_2": 110.0, "risk_reward": 2.0,
        "created_at": datetime(2026, 5, 1, 0, 0, tzinfo=UTC).isoformat(),
        "status": "OPEN", "result": "OPEN", "first_delivered_at": "2026-05-01T00:01:00+00:00",
    }


def fine(path: list[tuple[float, float]], filler: tuple[float, float], total: int = 192) -> list[dict]:
    bars = path + [filler] * (total - len(path))
    return [
        {"timestamp": START + timedelta(minutes=15 * i), "open": (hi + lo) / 2, "high": hi, "low": lo,
         "close": (hi + lo) / 2, "volume": 1.0}
        for i, (hi, lo) in enumerate(bars)
    ]


def coarse(fine_bars: list[dict]) -> list[dict]:
    out = []
    for i in range(0, len(fine_bars), 16):
        chunk = fine_bars[i:i + 16]
        out.append({"timestamp": chunk[0]["timestamp"], "open": chunk[0]["open"], "high": max(b["high"] for b in chunk),
                    "low": min(b["low"] for b in chunk), "close": chunk[-1]["close"], "volume": 16.0})
    return out


def run(fine_bars):
    ambiguous = replay_crypto(row(), coarse(fine_bars), first_delivery=row()["first_delivered_at"], as_of=AS_OF)
    assert ambiguous["status"] == "AMBIGUOUS"
    return ambiguous, refine_ambiguous(row(), ambiguous, fine_bars, first_delivery=row()["first_delivered_at"], as_of=AS_OF)


def test_entry_then_stop_inside_one_candle_becomes_a_loss():
    _, refined = run(fine([(101.0, 100.0), (99.0, 94.0)], filler=(94.5, 94.0)))
    assert (refined["status"], refined["result"], refined["pnl_r_multiple"]) == ("SL_HIT", "LOSS", -1.0)
    assert refined["entry_triggered_at"] == START.isoformat()
    assert refined["closed_at"] == (START + timedelta(hours=4)).isoformat()
    assert refined["candles_to_resolution"] == 1
    assert "INTRABAR_15M_REFINED" in refined["tracking_evidence"]["flags"]


def test_entry_then_both_targets_inside_one_candle_becomes_a_win():
    _, refined = run(fine([(101.0, 100.0), (106.0, 101.0), (111.0, 106.0)], filler=(111.0, 110.5)))
    assert (refined["status"], refined["result"]) == ("TP2_HIT", "WIN")
    assert refined["tp1_hit_at"] == refined["tp2_hit_at"] == (START + timedelta(hours=4)).isoformat()


def test_order_unknown_inside_one_15m_candle_stays_ambiguous():
    ambiguous, refined = run(fine([(106.0, 94.0)], filler=(94.5, 94.0)))
    assert refined is ambiguous


def test_missing_15m_candles_keep_the_original_outcome():
    bars = fine([(101.0, 100.0), (99.0, 94.0)], filler=(94.5, 94.0))
    ambiguous = replay_crypto(row(), coarse(bars), first_delivery=row()["first_delivered_at"], as_of=AS_OF)
    refined = refine_ambiguous(row(), ambiguous, bars[1:], first_delivery=row()["first_delivered_at"], as_of=AS_OF)
    assert refined is ambiguous


def test_refined_entry_keeps_the_previously_confirmed_bar():
    bars = fine([(101.0, 100.0)] * 16 + [(106.0, 101.0), (99.0, 94.0)], filler=(94.5, 94.0))
    before = replay_crypto(row(), coarse(bars)[:1], first_delivery=row()["first_delivered_at"], as_of=START + timedelta(hours=4))
    assert before["status"] == "OPEN" and before["entry_triggered_at"] == START.isoformat()
    _, refined = run(bars)
    guarded = guard_crypto_transition(dict(row(), **{k: before[k] for k in ("entry_triggered_at", "lifecycle_events")}), refined)
    assert guarded["status"] == "CLOSED_AFTER_TP1"
    assert round(guarded["pnl_r_multiple"], 4) == -0.0909
    assert guarded["tp1_hit_at"] == guarded["sl_hit_at"] == (START + timedelta(hours=8)).isoformat()


def test_candle_budget():
    assert intrabar_candles_needed(row(), AS_OF) == 280
    assert intrabar_candles_needed(dict(row(), timeframe="15m"), AS_OF) is None
    assert intrabar_candles_needed(row(), AS_OF + timedelta(days=90)) is None
