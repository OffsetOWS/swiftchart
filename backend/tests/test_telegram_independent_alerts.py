import asyncio
from types import SimpleNamespace

import pandas as pd

from app.models.schemas import TradeIdea


class FakeBot:
    def __init__(self) -> None:
        self.messages: list[tuple[int, str]] = []
        self.reply_markups = []

    async def send_message(self, chat_id: int, text: str, reply_markup=None) -> None:
        self.messages.append((chat_id, text))
        self.reply_markups.append(reply_markup)


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


def idea(symbol: str = "BTCUSDT", *, score: float = 80, entry_status: str = "READY", timeframe: str = "4h") -> TradeIdea:
    return TradeIdea(
        symbol=symbol,
        timeframe=timeframe,
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
    monkeypatch.setenv("ALERT_TIMEFRAME", "4h")
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
    buttons = [button.text for row in bot.reply_markups[0].inline_keyboard for button in row]
    assert buttons == ["🧪 Paper Trade"]


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


def test_telegram_only_sends_ready_confirmed_setups(monkeypatch, tmp_path):
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
    assert result["eligible"] == 1
    assert result["sent"] == 1
    assert len(bot.messages) == 1
    sent_text = "\n".join(message for _, message in bot.messages)
    assert "SOLUSDT" in sent_text
    assert "ETHUSDT" not in sent_text
    assert "DOGEUSDT" not in sent_text


def test_telegram_alerts_require_score_75_or_higher(monkeypatch, tmp_path):
    from bot.alerts import run_alert_scan

    configure_independent_scan(
        monkeypatch,
        tmp_path,
        {
            "HIGHUSDT": [idea("HIGHUSDT", score=75, entry_status="READY")],
            "LOWUSDT": [idea("LOWUSDT", score=74, entry_status="READY")],
        },
    )

    bot = FakeBot()
    result = asyncio.run(run_alert_scan(bot))

    assert result["min_score"] == 75
    assert result["ideas"] == 2
    assert result["limit_order_ideas"] == 2
    assert result["score_eligible_ideas"] == 1
    assert result["eligible"] == 1
    assert result["sent"] == 1
    assert result["skipped_by_score"] == 1
    assert result["skipped_low_score"] == 1
    assert result["rejection_reasons"]["skipped_low_score"] == 1
    sent_text = "\n".join(message for _, message in bot.messages)
    assert "HIGHUSDT" in sent_text
    assert "LOWUSDT" not in sent_text


def test_telegram_alerts_skip_unsupported_timeframes(monkeypatch, tmp_path):
    from bot.alerts import run_alert_scan

    configure_independent_scan(monkeypatch, tmp_path, {"BTCUSDT": [idea("BTCUSDT", timeframe="4h")]})
    monkeypatch.setenv("ALERT_TIMEFRAMES", "30m,4h,8h")

    bot = FakeBot()
    result = asyncio.run(run_alert_scan(bot))

    assert result["timeframes"] == ["4h"]
    assert result["skipped_timeframe"] == 2
    assert result["rejection_reasons"]["skipped_timeframe"] == 2
    assert result["sent"] == 1


def test_telegram_alerts_allow_requested_timeframes(monkeypatch, tmp_path):
    from bot.alerts import run_alert_scan

    ideas_by_symbol = {
        "BTCUSDT": [idea("BTCUSDT", timeframe="1h")],
        "ETHUSDT": [idea("ETHUSDT", timeframe="2h")],
        "SOLUSDT": [idea("SOLUSDT", timeframe="3h")],
        "AVAXUSDT": [idea("AVAXUSDT", timeframe="4h")],
        "LINKUSDT": [idea("LINKUSDT", timeframe="6h")],
    }
    configure_independent_scan(monkeypatch, tmp_path, ideas_by_symbol)
    monkeypatch.setenv("ALERT_TIMEFRAMES", "1h,2h,3h,4h,6h")
    monkeypatch.setenv("ALERT_SCAN_ALL_TIMEFRAMES_PER_RUN", "true")

    bot = FakeBot()
    result = asyncio.run(run_alert_scan(bot))

    assert result["timeframes"] == ["1h", "2h", "3h", "4h", "6h"]
    assert result["sent"] == 5
    sent_text = "\n".join(message for _, message in bot.messages)
    assert "BTCUSDT — 1H" in sent_text
    assert "ETHUSDT — 2H" in sent_text
    assert "SOLUSDT — 3H" in sent_text
    assert "AVAXUSDT — 4H" in sent_text
    assert "LINKUSDT — 6H" in sent_text
    assert "Strength: Fast Setup" in sent_text
    assert sent_text.count("Strength: Medium Setup") >= 2
    assert sent_text.count("Strength: Strong Setup") >= 2


def test_telegram_alerts_rotate_requested_timeframes_by_default(monkeypatch, tmp_path):
    import bot.alerts as alerts
    from bot.alerts import run_alert_scan

    alerts._alert_timeframe_cursor = 0
    configure_independent_scan(monkeypatch, tmp_path, {"BTCUSDT": [idea("BTCUSDT", timeframe="1h")]})
    monkeypatch.setenv("ALERT_TIMEFRAMES", "1h,2h,3h,4h,6h")

    bot = FakeBot()
    result = asyncio.run(run_alert_scan(bot))

    assert result["timeframes"] == ["1h"]


def test_telegram_alert_dedup_is_kept(monkeypatch, tmp_path):
    from bot.alerts import run_alert_scan

    configure_independent_scan(monkeypatch, tmp_path, {"AVAXUSDT": [idea("AVAXUSDT")]})

    bot = FakeBot()
    first = asyncio.run(run_alert_scan(bot))
    second = asyncio.run(run_alert_scan(bot))

    assert first["sent"] == 1
    assert second["sent"] == 0
    assert len(bot.messages) == 1


def test_trade_alert_formatter_only_includes_public_alert_fields():
    from bot.formatter import format_trade_alert

    trade_idea = idea("LDOUSDT", score=77)
    trade_idea.direction = "Short"
    trade_idea.entry_zone = (0.34201, 0.34512)
    trade_idea.stop_loss = 0.352031
    trade_idea.take_profit_1 = 0.328189
    trade_idea.take_profit_2 = 0.315749
    trade_idea.risk_reward_ratio = 3.29
    trade_idea.reason = (
        "Market structure favors trend-continuation pullbacks. Short idea has confirmed "
        "liquidity sweep/reclaim with quality score 100. Signal aligns with active bearish conditions. "
        "Entry Status: READY. Market Regime: Bearish trend (+42); trade is with-trend; confidence adjustment +4."
    )
    trade_idea.source = "hyperliquid"
    trade_idea.regime_label = "Bearish trend"
    trade_idea.regime_type = "TRENDING_DOWN"
    trade_idea.regime_confidence_score = 91
    trade_idea.regime_structure = "lower-highs"
    trade_idea.regime_trade_decision = "TRADE_ALLOWED"
    trade_idea.is_regime_transition = True
    trade_idea.trend_alignment = "with-trend"
    trade_idea.higher_timeframe_bias = "HTF_BEARISH"
    trade_idea.move_maturity = "Mid-Trend"
    trade_idea.exhaustion_risk = "Medium"
    trade_idea.entry_status = "READY"
    trade_idea.downgraded_reasons = ["debug-only reason"]
    trade_idea.invalid_condition = "Debug-only invalidation."

    message = format_trade_alert(trade_idea)

    assert message == (
        "SwiftChart Trade Alert: LDOUSDT — 4H\n\n"
        "Signal: Potential Short\n"
        "Strength: Strong Setup\n"
        "Setup Score: 77/100\n"
        "Grade: Valid Setup\n\n"
        "Entry: 0.34201 — 0.34512\n"
        "Stop Loss: 0.352031\n"
        "TP1: 0.328189\n"
        "TP2: 0.315749\n"
        "R:R: 3.29\n\n"
        "Reason:\n"
        "Market structure favors trend-continuation pullbacks. Short idea has confirmed "
        "liquidity sweep/reclaim with quality score 100."
    )
    removed_fields = [
        "Source:",
        "Market Regime:",
        "Regime Type:",
        "Regime Confidence:",
        "Structure:",
        "Transitioning:",
        "Decision:",
        "Move Maturity:",
        "Exhaustion Risk:",
        "Entry Status:",
        "Rejected/Downgraded Reasons:",
        "Trade Bias:",
        "HTF Bias:",
        "Invalid if:",
        "Not financial advice.",
    ]
    for field in removed_fields:
        assert field not in message


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
