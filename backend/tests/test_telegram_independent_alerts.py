import asyncio

from app.models.schemas import TradeIdea


def idea(symbol: str = "BTCUSDT", *, score: float = 80, timeframe: str = "4h") -> TradeIdea:
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
        entry_status="READY",
    )


def test_trade_alert_formatter_preserves_intentional_public_alert_fields():
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
        "R:R: 3.29\n"
        "Confidence: 77%\n"
        "Bias: Bearish trend\n"
        "BTC Context: -\n\n"
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
        "HTF Bias:",
        "Invalid if:",
        "Not financial advice.",
    ]
    for field in removed_fields:
        assert field not in message


def test_trade_alert_warns_when_direction_conflicts_with_bias():
    from bot.formatter import format_trade_alert

    trade_idea = idea("ARBUSDT", score=81)
    trade_idea.direction = "Long"
    trade_idea.regime_bias = "Short bias"

    message = format_trade_alert(
        trade_idea,
        {"regime": "bearish", "score_4h": -55, "score_1d": -30, "score": -42.5},
    )

    assert "Bias: Short bias" in message
    assert "BTC Context: Bearish | score -42.5 | 4H -55 | 1D -30" in message
    assert "⚠️ Direction conflicts with market bias." in message


def test_manual_telegram_market_discovery_remains_available_for_diagnostics(monkeypatch):
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
    assert "Non-authoritative" in telegram_scanner.scan_top_ideas.__doc__


def test_website_top_ideas_still_uses_shared_cache(monkeypatch):
    from app.routes import markets

    async def fake_cached_top_ideas(exchange: str, timeframe: str):
        return {"exchange": exchange, "timeframe": timeframe, "ideas": [], "scan_stats": {"valid_setups": 0}}

    monkeypatch.setattr(markets, "cached_top_ideas", fake_cached_top_ideas)

    result = asyncio.run(markets.top_ideas(exchange="hyperliquid", timeframe="4h", symbols=None))

    assert result["exchange"] == "hyperliquid"
