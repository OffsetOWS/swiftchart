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
        f"Market Condition: {analysis.market_condition}\n"
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
            "No clean setups found right now.\n\n"
            f"{RISK_WARNING}"
        )

    lines = [f"SwiftChart Top 5 — {timeframe.upper()} ({exchange})"]
    for index, idea in enumerate(ideas, start=1):
        lines.append(
            "\n"
            f"{index}. {idea.symbol} — {idea.direction}\n"
            f"Entry: {fmt_zone(idea.entry_zone)}\n"
            f"SL: {fmt(idea.stop_loss)} | TP1: {fmt(idea.take_profit_1)} | TP2: {fmt(idea.take_profit_2)}\n"
            f"R:R: {fmt(idea.risk_reward_ratio)} | Confidence: {fmt(idea.confidence_score)}%\n"
            f"Reason: {idea.reason}"
        )
    lines.append(f"\n{RISK_WARNING}")
    return "\n".join(lines)


def strategy_text() -> str:
    return (
        "SwiftChart Strategy\n\n"
        "SwiftChart detects support/resistance, liquidity sweeps, range conditions, breakouts, "
        "and avoids mid-range entries.\n\n"
        "Simple version:\n"
        "Buy near support, sell near resistance, avoid the middle, and wait for liquidity sweeps.\n\n"
        f"{RISK_WARNING}"
    )


def help_text() -> str:
    return (
        "SwiftChart Bot Commands\n\n"
        "/start — Open the main menu\n"
        "/analyze SOLUSDT 4h — Analyze a coin and timeframe\n"
        "/top — Show current top 5 trade ideas\n"
        "/strategy — Explain the strategy\n"
        "/help — Show commands\n\n"
        "Supported timeframes: 30m, 1h, 2h, 4h, 6h, 8h, 12h, 1D\n\n"
        f"{RISK_WARNING}"
    )
