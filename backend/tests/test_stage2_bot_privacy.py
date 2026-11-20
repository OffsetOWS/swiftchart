import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot import handlers
from bot.formatter import format_history, format_paper_trades, format_stats
from app.services import trade_history


def test_stats_command_does_not_fetch_or_expose_internal_statistics(monkeypatch):
    def forbidden():
        raise AssertionError("Public bot command must not fetch performance")
    monkeypatch.setattr(trade_history, "stats", forbidden)
    message=SimpleNamespace(reply_text=AsyncMock())
    asyncio.run(handlers.stats_command(SimpleNamespace(effective_message=message),None))
    message.reply_text.assert_awaited_once_with("Performance statistics are unavailable.")


def test_old_stats_formatter_does_not_read_its_input():
    class Unreadable(dict):
        def __getitem__(self,key):raise AssertionError("Metrics must not be read")
        def get(self,*args):raise AssertionError("Metrics must not be read")
    assert format_stats(Unreadable())=="Performance statistics are unavailable."


def test_history_and_paper_messages_omit_realized_performance_even_when_certified():
    history={"symbol":"BTCUSDT","timeframe":"4h","direction":"LONG","exchange":"hyperliquid",
             "status":"TP2_HIT","setup_score":80,"take_profit_1":110,"take_profit_2":120,"stop_loss":90,
             "gross_r_multiple":987.654321,"net_r_multiple":876.543219,"cost_status":"MODELED"}
    paper={"pair":"BTCUSDT","side":"long","entry":100,"stop_loss":90,"tp1":110,"tp2":120,
           "status":"closed","pnl_r":987.654321,"net_r_multiple":876.543219,"cost_status":"MODELED"}
    for text in [format_history([history]),format_paper_trades([paper])]:
        assert "987" not in text and "876" not in text
        for label in ("Gross modeled", "Net modeled", "Result:", "Costs:", "win rate", "expectancy"):
            assert label not in text
        assert "BTCUSDT" in text and "110" in text
