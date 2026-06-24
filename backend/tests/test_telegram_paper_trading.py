from bot.formatter import format_paper_trades
import asyncio
from types import SimpleNamespace

from bot.paper_trading import evaluate_trade, list_open_paper_trades
from bot.storage import get_latest_signal, save_signal


def trade(**overrides):
    value = {
        "side": "long",
        "status": "open",
        "entry": 100,
        "stop_loss": 95,
        "tp1": 110,
        "tp2": 120,
    }
    value.update(overrides)
    return value


def test_long_trade_hits_tp1_and_stays_active():
    result = evaluate_trade(trade(), high=111, low=99)

    assert result["status"] == "tp1_hit"
    assert result["pnl_r"] == 2
    assert "closed_at" not in result


def test_long_trade_hits_tp2_and_closes():
    result = evaluate_trade(trade(), high=121, low=99)

    assert result["status"] == "tp2_hit"
    assert result["pnl_r"] == 4
    assert result["closed_at"]


def test_short_trade_hits_stop():
    result = evaluate_trade(
        trade(side="short", entry=100, stop_loss=105, tp1=90, tp2=80),
        high=106,
        low=99,
    )

    assert result["status"] == "sl_hit"
    assert result["pnl_r"] == -1
    assert result["closed_at"]


def test_same_candle_stop_and_target_uses_conservative_stop_first():
    result = evaluate_trade(trade(), high=121, low=94)

    assert result["status"] == "sl_hit"
    assert result["pnl_r"] == -1


def test_my_trades_formatter_includes_requested_fields():
    message = format_paper_trades(
        [
            {
                "pair": "BTCUSDT",
                "side": "long",
                "entry": 100,
                "stop_loss": 95,
                "tp1": 110,
                "tp2": 120,
                "status": "tp2_hit",
                "pnl_r": 4,
                "opened_at": "2026-06-23T10:00:00+00:00",
                "closed_at": "2026-06-23T12:00:00+00:00",
            }
        ]
    )

    for value in ("BTCUSDT", "LONG", "Entry: 100", "SL: 95", "TP1: 110", "TP2: 120", "tp2_hit", "4R"):
        assert value in message


def test_open_trades_formatter_has_open_heading_and_empty_state():
    assert format_paper_trades([], open_only=True) == "SwiftChart Open Paper Trades\n\nNo open paper trades."


def test_latest_signal_can_be_selected_by_pair_or_signal_id(monkeypatch, tmp_path):
    monkeypatch.setenv("BOT_STATE_PATH", str(tmp_path / "state.json"))
    save_signal(
        "swiftchart-old",
        {
            "signal_id": "swiftchart-old",
            "pair": "BTCUSDT",
            "analysis": "Old BTC analysis",
            "saved_at": "2026-06-23T10:00:00+00:00",
        },
    )
    save_signal(
        "swiftchart-new",
        {
            "signal_id": "swiftchart-new",
            "pair": "ETHUSDT",
            "analysis": "New ETH analysis",
            "saved_at": "2026-06-23T11:00:00+00:00",
        },
    )

    assert get_latest_signal()["signal_id"] == "swiftchart-new"
    assert get_latest_signal("BTCUSDT")["signal_id"] == "swiftchart-old"
    assert get_latest_signal("swiftchart-new")["pair"] == "ETHUSDT"


def test_open_trade_query_filters_to_active_user_trades(monkeypatch):
    captured = {}

    async def fake_request(method, path, **kwargs):
        captured.update({"method": method, "path": path, **kwargs})
        return SimpleNamespace(json=lambda: [])

    monkeypatch.setattr("bot.paper_trading._request", fake_request)

    assert asyncio.run(list_open_paper_trades(123)) == []
    assert captured["params"]["telegram_user_id"] == "eq.123"
    assert captured["params"]["status"] == "in.(open,tp1_hit)"
