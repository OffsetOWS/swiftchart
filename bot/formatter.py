from app.models.schemas import AnalysisResponse, TradeIdea

RISK_WARNING = "Not financial advice. Manage your risk."


def fmt(value: float | int | None) -> str:
    if value is None:
        return "-"
    return f"{value:,.6f}".rstrip("0").rstrip(".")


def fmt_zone(zone: tuple[float, float] | None) -> str:
    if not zone:
        return "-"
    return f"{fmt(zone[0])} — {fmt(zone[1])}"


def _zone_range(zones) -> str:
    if not zones:
        return "-"
    strongest = zones[0]
    return f"{fmt(strongest.lower)} — {fmt(strongest.upper)}"


def signal_label(idea: TradeIdea | None) -> str:
    if idea is None:
        return "No Trade"
    return f"Potential {idea.direction}"


def format_analysis(analysis: AnalysisResponse) -> str:
    idea = analysis.trade_ideas[0] if analysis.trade_ideas else None
    timeframe = analysis.timeframe.upper()

    if idea is None:
        trade_block = (
            "Signal: No Trade\n"
            "Setup Score: -\n"
            "Grade: No Trade\n"
            f"HTF Bias: {analysis.higher_timeframe_bias}\n"
            "Entry: -\n"
            "Stop Loss: -\n"
            "TP1: -\n"
            "TP2: -\n"
            "R:R: -\n"
            "Confidence: -"
        )
        reason = analysis.warning or "Price is mid-range or the setup is unclear."
        invalid = "Wait for a clean range edge, sweep reclaim, or confirmed breakout."
    else:
        trade_block = (
            f"Signal: {signal_label(idea)}\n"
            f"Setup Score: {fmt(idea.setup_score or idea.confidence_score)}/100\n"
            f"Grade: {idea.setup_grade or 'Valid Setup'}\n"
            f"HTF Bias: {idea.higher_timeframe_bias}\n"
            f"Entry: {fmt_zone(idea.entry_zone)}\n"
            f"Stop Loss: {fmt(idea.stop_loss)}\n"
            f"TP1: {fmt(idea.take_profit_1)}\n"
            f"TP2: {fmt(idea.take_profit_2)}\n"
            f"R:R: {fmt(idea.risk_reward_ratio)}\n"
            f"Confidence: {fmt(idea.confidence_score)}%"
        )
        reason = idea.reason
        invalid = idea.invalid_condition

    return (
        f"SwiftChart Analysis: {analysis.symbol} — {timeframe}\n\n"
        f"Market Regime: {analysis.market_condition}\n"
        f"Support Zone: {_zone_range(analysis.support_zones)}\n"
        f"Resistance Zone: {_zone_range(analysis.resistance_zones)}\n"
        f"{trade_block}\n\n"
        f"Reason:\n{reason}\n\n"
        f"Invalid if:\n{invalid}\n\n"
        f"{RISK_WARNING}"
    )


def format_top_ideas(ideas: list[TradeIdea], timeframe: str, exchange: str) -> str:
    if not ideas:
        return (
            f"SwiftChart Top 5 — {timeframe.upper()} ({exchange})\n\n"
            "Only 0 valid setups found. Other coins are currently no-trade.\n\n"
            f"{RISK_WARNING}"
        )

    lines = [f"SwiftChart Top 5 — {timeframe.upper()} ({exchange})"]
    if len(ideas) < 5:
        lines.append(f"Only {len(ideas)} valid setups found. Other coins are currently no-trade.")
    for index, idea in enumerate(ideas, start=1):
        lines.append(
            "\n"
            f"{index}. {idea.symbol} — {idea.direction}\n"
            f"Score: {fmt(idea.setup_score or idea.confidence_score)}/100 | Grade: {idea.setup_grade or 'Valid Setup'}\n"
            f"Regime: {idea.market_regime or '-'} | HTF: {idea.higher_timeframe_bias}\n"
            f"Entry: {fmt_zone(idea.entry_zone)}\n"
            f"SL: {fmt(idea.stop_loss)} | TP1: {fmt(idea.take_profit_1)} | TP2: {fmt(idea.take_profit_2)}\n"
            f"R:R: {fmt(idea.risk_reward_ratio)} | Confidence: {fmt(idea.confidence_score)}%\n"
            f"Reason: {idea.reason}"
        )
    lines.append(f"\n{RISK_WARNING}")
    return "\n".join(lines)


def format_trade_alert(idea: TradeIdea) -> str:
    return (
        f"SwiftChart Trade Alert: {idea.symbol} — {idea.timeframe.upper()}\n\n"
        f"Signal: Potential {idea.direction}\n"
        f"Setup Score: {fmt(idea.setup_score or idea.confidence_score)}/100\n"
        f"Grade: {idea.setup_grade or 'Valid Setup'}\n"
        f"Market Regime: {idea.market_regime or '-'}\n"
        f"HTF Bias: {idea.higher_timeframe_bias}\n\n"
        f"Entry: {fmt_zone(idea.entry_zone)}\n"
        f"Stop Loss: {fmt(idea.stop_loss)}\n"
        f"TP1: {fmt(idea.take_profit_1)}\n"
        f"TP2: {fmt(idea.take_profit_2)}\n"
        f"R:R: {fmt(idea.risk_reward_ratio)}\n\n"
        f"Reason:\n{idea.reason}\n\n"
        f"Invalid if:\n{idea.invalid_condition}\n\n"
        f"{RISK_WARNING}"
    )


def format_history(records: list[dict]) -> str:
    if not records:
        return "SwiftChart History\n\nNo saved trade ideas yet."

    lines = ["SwiftChart History — Latest 5"]
    for record in records[:5]:
        r_multiple = record.get("pnl_r_multiple")
        lines.append(
            "\n"
            f"{record['symbol']} — {record['timeframe'].upper()} — {record['direction']} ({record.get('exchange', '-')})\n"
            f"Status: {record['status']} | Result: {record['result']}\n"
            f"Setup Score: {fmt(record.get('setup_score'))}/100\n"
            f"TP/SL: TP1 {fmt(record['take_profit_1'])} | TP2 {fmt(record['take_profit_2'])} | SL {fmt(record['stop_loss'])}\n"
            f"R Multiple: {fmt(r_multiple)}"
        )
    lines.append(f"\n{RISK_WARNING}")
    return "\n".join(lines)


def format_stats(data: dict) -> str:
    return (
        "SwiftChart Performance Stats\n\n"
        f"Total setups: {data['total_ideas']}\n"
        f"Win rate: {fmt(data['win_rate'])}%\n"
        f"TP hit rate: {fmt(data['tp_hit_rate'])}%\n"
        f"SL hit rate: {fmt(data['sl_hit_rate'])}%\n"
        f"Average R: {fmt(data['average_r_multiple'])}\n"
        f"Open setups: {data['open_count']}\n"
        f"Ambiguous: {data['ambiguous_count']}\n\n"
        f"{RISK_WARNING}"
    )


def strategy_text() -> str:
    return (
        "SwiftChart Strategy\n\n"
        "SwiftChart classifies market regime first, scores support/resistance zones, confirms liquidity sweeps, "
        "checks higher-timeframe bias, and rejects unclear or mid-range setups.\n\n"
        "Simple version:\n"
        "Buy near support, sell near resistance, avoid the middle, and wait for liquidity sweeps.\n"
        "Only setups scoring 65/100 or higher are shown.\n\n"
        f"{RISK_WARNING}"
    )


def help_text() -> str:
    return (
        "SwiftChart Bot Commands\n\n"
        "/start — Open the main menu\n"
        "/analyze SOLUSDT 4h — Analyze a coin and timeframe\n"
        "/top — Show current top 5 trade ideas\n"
        "/subscribe — Get Telegram alerts when valid setups appear\n"
        "/unsubscribe — Stop Telegram alerts\n"
        "/history — Show latest saved trade ideas and outcomes\n"
        "/stats — Show performance summary\n"
        "/checktrades — Manually update saved outcomes\n"
        "/strategy — Explain the strategy\n"
        "/help — Show commands\n\n"
        "Supported timeframes: 30m, 1h, 2h, 4h, 6h, 8h, 12h, 1D\n\n"
        f"{RISK_WARNING}"
    )
