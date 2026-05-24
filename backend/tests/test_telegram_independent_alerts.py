import asyncio
from types import SimpleNamespace

import pandas as pd

from app.models.schemas import TradeIdea


class FakeBot:
    def __init__(self) -> None:
        self.messages: list[tuple[int, str]] = []

    async def send_message(self, chat_id: int, text: str) -> None:
        self.messages.append((chat_id, text))


class FakeExchange:
    async def get_candles(self, symbol: str, timeframe: str, limit: int):
        return pd.DataFrame(
            {
                "open": [100.0] * 80,
                "high": [101.0] * 80,
                "low": [99.0] * 80,
                "close": [100.0] * 80,
                "volume": [1_000.0] * 80,
            }
        )


def idea(symbol: str = "BTCUSDT", *, score: float = 80, entry_status: str = "READY") -> TradeIdea:
    return TradeIdea(
        symbol=symbol,
        timeframe="4h",
        exchange="hyperliquid",
        source="hyperliquid",
        direction="Long",
        entry_zone=(100.0, 101.0),
        stop_loss=96.0,
        take_profit_1=110.0,
        take_profit_2=118.0,
        risk_reward_ratio=2.5,
        reason="Clean SwiftChart setup.",
        confidence_score=score,
        setup_score=score,
        invalid_condition="Break below support.",
        rank_score=score,
        entry_status=entry_status,
    )


def configure_independent_scan(monkeypatch, tmp_path, ideas_by_symbol: dict[str, list[TradeIdea]]) -> None:
    import bot.scanner as telegram_scanner

    monkeypatch.setenv("BOT_STATE_PATH", str(tmp_path / "bot_state.json"))
    monkeypatch.setenv("ALERT_DEDUPE_STATE_PATH", str(tmp_path / "alert_dedupe.json"))
    monkeypatch.setenv("TELEGRAM_ALERT_CHAT_IDS", "123")
    monkeypatch.setenv("ALERT_MIN_SCORE", "75")
    monkeypatch.setattr(telegram_scanner, "DEFAULT_SCAN_LIST", list(ideas_by_symbol))
    async def fake_scan_symbols(exchange):
        return list(ideas_by_symbol)

    monkeypatch.setattr(telegram_scanner, "_scan_symbols", fake_scan_symbols)
    monkeypatch.setattr(telegram_scanner, "get_exchange", lambda exchange: FakeExchange())
    async def fake_get_candles_cached(exchange, symbol, timeframe, limit):
        return await FakeExchange().get_candles(symbol, timeframe, limit)

    monkeypatch.setattr(telegram_scanner, "get_candles_cached", fake_get_candles_cached)

    def fake_analyze_dataframe(symbol, timeframe, exchange, df, risk):
        return SimpleNamespace(
            trade_ideas=ideas_by_symbol.get(symbol, []),
            rejected_signals=[],
            no_trade_reason=None,
        )

    monkeypatch.setattr(telegram_scanner, "analyze_dataframe", fake_analyze_dataframe)


def test_telegram_alert_loop_works_when_website_ideas_are_empty(monkeypatch, tmp_path):
    from app.services import scanner as website_scanner
    from bot.alerts import run_alert_scan

    async def empty_cached_top_ideas(*args, **kwargs):
        return {"ideas": []}

    monkeypatch.setattr(website_scanner, "cached_top_ideas", empty_cached_top_ideas)
    configure_independent_scan(monkeypatch, tmp_path, {"XRPUSDT": [idea("XRPUSDT")]})

    bot = FakeBot()
    result = asyncio.run(run_alert_scan(bot))

    assert result["ideas"] == 1
    assert result["eligible"] == 1
    assert result["sent"] == 1
    assert len(bot.messages) == 1


def test_telegram_does_not_call_cached_top_ideas(monkeypatch, tmp_path):
    from app.services import scanner as website_scanner
    from bot.alerts import run_alert_scan

    async def fail_if_called(*args, **kwargs):
        raise AssertionError("Telegram must not call cached_top_ideas")

    monkeypatch.setattr(website_scanner, "cached_top_ideas", fail_if_called)
    configure_independent_scan(monkeypatch, tmp_path, {"BTCUSDT": [idea("BTCUSDT")]})

    bot = FakeBot()
    result = asyncio.run(run_alert_scan(bot))

    assert result["sent"] == 1


def test_telegram_sends_limit_order_setups(monkeypatch, tmp_path):
    from bot.alerts import run_alert_scan

    configure_independent_scan(
        monkeypatch,
        tmp_path,
        {
            "SOLUSDT": [idea("SOLUSDT", entry_status="READY")],
            "ETHUSDT": [idea("ETHUSDT", score=95, entry_status="WAIT_FOR_RETEST")],
            "DOGEUSDT": [idea("DOGEUSDT", score=95, entry_status="REJECTED_EXHAUSTED")],
        },
    )

    bot = FakeBot()
    result = asyncio.run(run_alert_scan(bot))

    assert result["ideas"] == 3
    assert result["eligible"] == 2
    assert result["sent"] == 2
    assert len(bot.messages) == 2
    sent_text = "\n".join(message for _, message in bot.messages)
    assert "SOLUSDT" in sent_text
    assert "ETHUSDT" in sent_text
    assert "DOGEUSDT" not in sent_text


def test_telegram_alert_dedup_is_kept(monkeypatch, tmp_path):
    from bot.alerts import run_alert_scan

    configure_independent_scan(monkeypatch, tmp_path, {"AVAXUSDT": [idea("AVAXUSDT")]})

    bot = FakeBot()
    first = asyncio.run(run_alert_scan(bot))
    second = asyncio.run(run_alert_scan(bot))

    assert first["sent"] == 1
    assert second["sent"] == 0
    assert len(bot.messages) == 1


def test_telegram_market_discovery_does_not_filter_low_liquidity(monkeypatch):
    import bot.scanner as telegram_scanner

    async def fake_markets(exchange):
        return [
            {"symbol": "LOWUSDT", "active": True, "perpVolume24h": 1},
            {"symbol": "ZEROUSDT", "active": True, "perpVolume24h": 0},
            {"symbol": "OFFUSDT", "active": False, "perpVolume24h": 1_000_000},
        ]

    monkeypatch.setenv("TELEGRAM_SCAN_SYMBOL_LIMIT", "50")
    monkeypatch.setattr(telegram_scanner, "get_markets_cached", fake_markets)

    symbols = asyncio.run(telegram_scanner._scan_symbols("hyperliquid"))

    assert "LOWUSDT" in symbols
    assert "ZEROUSDT" in symbols
    assert "OFFUSDT" not in symbols


def test_website_top_ideas_still_uses_shared_cache(monkeypatch):
    from app.routes import markets

    async def fake_cached_top_ideas(exchange: str, timeframe: str):
        return {"exchange": exchange, "timeframe": timeframe, "ideas": [], "scan_stats": {"valid_setups": 0}}

    monkeypatch.setattr(markets, "cached_top_ideas", fake_cached_top_ideas)

    result = asyncio.run(markets.top_ideas(exchange="hyperliquid", timeframe="4h", symbols=None))

    assert result["exchange"] == "hyperliquid"
    assert result["ideas"] == []
    assert result["scan_stats"]["valid_setups"] == 0
