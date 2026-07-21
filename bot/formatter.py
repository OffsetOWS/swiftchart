from app.models.schemas import AnalysisResponse, TradeIdea

RISK_WARNING = "Not financial advice. Manage your risk."
REMOVED_PUBLIC_FIELD_LABELS = (
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
)


def fmt(value: float | int | None) -> str:
    if value is None:
        return "-"
    return f"{value:,.6f}".rstrip("0").rstrip(".")


def fmt_zone(zone: tuple[float, float] | None) -> str:
    if not zone:
        return "-"
    return f"{fmt(zone[0])} — {fmt(zone[1])}"


def public_reason_summary(reason: str | None, max_sentences: int | None = None) -> str:
    if not reason:
        return "-"

    cleaned_parts = []
    for part in reason.replace("\n", " ").split(". "):
        sentence = part.strip().rstrip(".")
        if not sentence:
            continue
        if any(label in sentence for label in REMOVED_PUBLIC_FIELD_LABELS):
            continue
        cleaned_parts.append(sentence)
        if max_sentences is not None and len(cleaned_parts) >= max_sentences:
            break

    return ". ".join(cleaned_parts) + "." if cleaned_parts else "-"


def _zone_range(zones) -> str:
    if not zones:
        return "-"
    strongest = zones[0]
    return f"{fmt(strongest.lower)} — {fmt(strongest.upper)}"


def signal_label(idea: TradeIdea | None) -> str:
    if idea is None:
        return "No Trade"
    return f"Potential {idea.direction}"


def source_label(idea: TradeIdea | None) -> str:
    source = (idea.source or idea.exchange if idea else "").lower()
    if source == "variational":
        return "Variational"
    if source == "hyperliquid":
        return "Hyperliquid"
    return source or "-"


def alert_strength_label(timeframe: str) -> str:
    normalized = timeframe.lower()
    if normalized == "1h":
        return "Fast Setup"
    if normalized in {"2h", "3h"}:
        return "Medium Setup"
    if normalized in {"4h", "6h"}:
        return "Strong Setup"
    return "Valid Setup"


def direction_conflicts_bias(direction: str | None, bias: str | None) -> bool:
    normalized_direction = str(direction or "").lower()
    normalized_bias = str(bias or "").lower()
    if normalized_direction == "long":
        return "short bias" in normalized_bias or "bearish transition" in normalized_bias
    if normalized_direction == "short":
        return "long bias" in normalized_bias or "bullish transition" in normalized_bias
    return False


def btc_context_line(btc_context: dict | None) -> str:
    if not btc_context:
        return "BTC Context: -"
    regime = btc_context.get("regime") or "-"
    score_4h = btc_context.get("score_4h")
    score_1d = btc_context.get("score_1d")
    score = btc_context.get("score")
    parts = [str(regime).title()]
    if score is not None:
        parts.append(f"score {fmt(score)}")
    if score_4h is not None:
        parts.append(f"4H {fmt(score_4h)}")
    if score_1d is not None:
        parts.append(f"1D {fmt(score_1d)}")
    return f"BTC Context: {' | '.join(parts)}"


def regime_line(idea: TradeIdea | None, analysis: AnalysisResponse | None = None) -> str:
    regime = idea.regime_label if idea else analysis.market_regime_data.label if analysis and analysis.market_regime_data else None
    confidence = idea.regime_confidence_score if idea else analysis.market_regime_data.confidence_score if analysis and analysis.market_regime_data else None
    regime_type = idea.regime_type if idea else analysis.market_regime_data.regime_type if analysis and analysis.market_regime_data else None
    if regime and confidence is not None:
        return f"{regime_type or regime} — {confidence:.0f}%"
    return str(regime or "-")


def regime_context(idea: TradeIdea | None, analysis: AnalysisResponse | None = None) -> str:
    data = analysis.market_regime_data if analysis else None
    regime_type = idea.regime_type if idea else data.regime_type if data else None
    confidence = idea.regime_confidence_score if idea else data.confidence_score if data else None
    structure = idea.regime_structure if idea else data.structure if data else None
    transition = idea.is_regime_transition if idea else data.is_transition if data else False
    decision = idea.regime_trade_decision if idea else data.trade_decision if data else None
    confidence_text = f"{confidence:.0f}%" if confidence is not None else "-"
    return (
        f"Regime Type: {regime_type or '-'}\n"
        f"Regime Confidence: {confidence_text}\n"
        f"Structure: {structure or '-'}\n"
        f"Transitioning: {'Yes' if transition else 'No'}\n"
        f"Decision: {decision or '-'}"
    )


def quality_context(idea: TradeIdea | None) -> str:
    if idea is None:
        return (
            "Move Maturity: -\n"
            "Exhaustion Risk: -\n"
            "Entry Status: -\n"
            "Rejected/Downgraded Reasons: -"
        )
    reasons = "; ".join(idea.downgraded_reasons) if idea.downgraded_reasons else "-"
    return (
        f"Move Maturity: {idea.move_maturity}\n"
        f"Exhaustion Risk: {idea.exhaustion_risk}\n"
        f"Entry Status: {idea.entry_status}\n"
        f"Rejected/Downgraded Reasons: {reasons}"
    )


def format_analysis(analysis: AnalysisResponse) -> str:
    idea = analysis.trade_ideas[0] if analysis.trade_ideas else None
    timeframe = analysis.timeframe.upper()

    if idea is None:
        trade_block = (
            "Signal: No Trade\n"
            "Setup Score: -\n"
            "Grade: No Trade\n"
            "Entry: -\n"
            "Stop Loss: -\n"
            "TP1: -\n"
            "TP2: -\n"
            "R:R: -\n"
            "Confidence: -"
        )
        reason = analysis.no_trade_reason or analysis.warning or "Price is mid-range or the setup is unclear."
        invalid = "Wait for a clean range edge, sweep reclaim, or confirmed breakout."
    else:
        trade_block = (
            f"Signal: {signal_label(idea)}\n"
            f"Setup Score: {fmt(idea.setup_score or idea.confidence_score)}/100\n"
            f"Grade: {idea.setup_grade or 'Valid Setup'}\n"
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
        f"Support Zone: {_zone_range(analysis.support_zones)}\n"
        f"Resistance Zone: {_zone_range(analysis.resistance_zones)}\n"
        f"{trade_block}\n\n"
        f"Reason:\n{public_reason_summary(reason)}\n\n"
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
            f"Entry: {fmt_zone(idea.entry_zone)}\n"
            f"SL: {fmt(idea.stop_loss)} | TP1: {fmt(idea.take_profit_1)} | TP2: {fmt(idea.take_profit_2)}\n"
            f"R:R: {fmt(idea.risk_reward_ratio)} | Confidence: {fmt(idea.confidence_score)}%\n"
            f"Reason: {public_reason_summary(idea.reason)}"
        )
    lines.append(f"\n{RISK_WARNING}")
    return "\n".join(lines)


def format_trade_alert(idea: TradeIdea, btc_context: dict | None = None) -> str:
    bias = idea.regime_bias or idea.regime_label or idea.higher_timeframe_bias or "-"
    warning = "\n⚠️ Direction conflicts with market bias." if direction_conflicts_bias(idea.direction, bias) else ""
    return (
        f"SwiftChart Trade Alert: {idea.symbol} — {idea.timeframe.upper()}\n\n"
        f"Signal: Potential {idea.direction}\n"
        f"Strength: {alert_strength_label(idea.timeframe)}\n"
        f"Setup Score: {fmt(idea.setup_score or idea.confidence_score)}/100\n"
        f"Grade: {idea.setup_grade or 'Valid Setup'}\n"
        "\n"
        f"Entry: {fmt_zone(idea.entry_zone)}\n"
        f"Stop Loss: {fmt(idea.stop_loss)}\n"
        f"TP1: {fmt(idea.take_profit_1)}\n"
        f"TP2: {fmt(idea.take_profit_2)}\n"
        f"R:R: {fmt(idea.risk_reward_ratio)}\n"
        f"Confidence: {fmt(idea.confidence_score)}%\n"
        f"Bias: {bias}\n"
        f"{btc_context_line(btc_context)}"
        f"{warning}\n\n"
        f"Reason:\n{public_reason_summary(idea.reason, max_sentences=2)}"
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


def format_paper_trades(records: list[dict], *, open_only: bool = False) -> str:
    title = "SwiftChart Open Paper Trades" if open_only else "SwiftChart Paper Trades"
    if not records:
        empty = "No open paper trades." if open_only else "No paper trades yet. Tap 🧪 Paper Trade on a signal to start one."
        return f"{title}\n\n{empty}"

    lines = [title]
    for record in records:
        lines.append(
            "\n"
            f"{record.get('pair', '-')} — {str(record.get('side', '-')).upper()}\n"
            f"Entry: {fmt(record.get('entry'))} | SL: {fmt(record.get('stop_loss'))}\n"
            f"TP1: {fmt(record.get('tp1'))} | TP2: {fmt(record.get('tp2'))}\n"
            f"Status: {record.get('status', '-')} | PnL: {fmt(record.get('pnl_r'))}R\n"
            f"Opened: {record.get('opened_at') or '-'}\n"
            f"Closed: {record.get('closed_at') or '-'}"
        )
    lines.append("\nSimulated trades only. No real orders are placed.")
    return "\n".join(lines)


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
        "/analysis [pair or signal ID] — View latest or selected signal analysis\n"
        "/mytrades — Show your Telegram paper trades\n"
        "/open — Show your open paper trades\n"
        "/checktrades — Manually update saved outcomes\n"
        "/strategy — Explain the strategy\n"
        "/help — Show commands\n\n"
        "Supported timeframes: 30m, 1h, 2h, 4h, 6h, 8h, 12h, 1D\n\n"
        f"{RISK_WARNING}"
    )
