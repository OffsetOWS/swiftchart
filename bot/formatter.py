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


def _format_reason_prices(reason: str, decimals: int | None) -> str:
    """Format evidence levels using the same tick precision as the trade plan."""
    if decimals is None:
        return reason
    import re

    def replace(match):
        value = float(match.group(0).replace(",", ""))
        return f"{value:,.{decimals}f}"

    return re.sub(r"(?<![\w.])\d[\d,]*(?:\.\d+)?(?![\w.])", replace, reason)


def public_reason_summary(reason: str | None, max_sentences: int | None = None) -> str:
    if not reason:
        return "-"

    cleaned_parts = []
    for part in reason.replace("\n", " ").split(". "):
        sentence = part.strip().rstrip(".")
        if not sentence:
            continue
        if any(word in sentence.lower() for word in ("score", "grade", "confidence", "profit", "win rate", "performance", "streak", "expectancy", "downgraded", "atr")) or any(label in sentence for label in REMOVED_PUBLIC_FIELD_LABELS):
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
    return "BTC Context: " + (str(btc_context.get("regime") or "Unavailable").title() if btc_context else "Unavailable")


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
            "Entry: -\n"
            "Stop Loss: -\n"
            "TP1: -\n"
            "TP2: -\n"
            "R:R: -\n"
        )
        reason = analysis.no_trade_reason or analysis.warning or "Price is mid-range or the setup is unclear."
        invalid = "Wait for a clean range edge, sweep reclaim, or confirmed breakout."
    else:
        trade_block = (
            f"Signal: {signal_label(idea)}\n"
            f"Entry: {fmt_zone(idea.entry_zone)}\n"
            f"Stop Loss: {fmt(idea.stop_loss)}\n"
            f"TP1: {fmt(idea.take_profit_1)}\n"
            f"TP2: {fmt(idea.take_profit_2)}\n"
            f"R:R: {fmt(idea.risk_reward_ratio)}\n"
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
            f"Entry: {fmt_zone(idea.entry_zone)}\n"
            f"SL: {fmt(idea.stop_loss)} | TP1: {fmt(idea.take_profit_1)} | TP2: {fmt(idea.take_profit_2)}\n"
            f"R:R: {fmt(idea.risk_reward_ratio)}\n"
            f"Reason: {public_reason_summary(idea.reason)}"
        )
    lines.append(f"\n{RISK_WARNING}")
    return "\n".join(lines)


def alert_symbol(symbol: str) -> str:
    pair = symbol.upper().split(":", 1)[0]
    if "/" not in pair:
        for quote in ("USDT", "USDC", "USD"):
            if pair.endswith(quote) and len(pair) > len(quote):
                return f"{pair[:-len(quote)]}/{quote}"
    return pair


def format_trade_alert(idea: TradeIdea, btc_context: str | dict | None = None) -> str:
    from html import escape

    is_bounce = idea.setup_family == "bounce_long"
    is_trend = idea.setup_family == "trend_continuation"
    direction = str(idea.direction).lower()
    is_long = direction == "long"
    if is_bounce:
        tag, color = "BOUNCE LONG", "🔵"
    elif is_trend and is_long:
        tag, color = "TREND LONG", "🟢"
    elif is_trend and not is_long:
        tag, color = "TREND SHORT", "🔴"
    else:
        tag, color = str(idea.direction).upper(), ""
    decimals = idea.price_decimals
    safe_symbol = escape(alert_symbol(idea.symbol), quote=False)
    coin = escape(alert_symbol(idea.symbol).split("/")[0], quote=False)
    price_fmt = lambda value: fmt(value) if decimals is None else f"{value:,.{decimals}f}"
    entry = (price_fmt(idea.entry_zone[0]) if idea.entry_zone[0] == idea.entry_zone[1]
             else " — ".join(price_fmt(value) for value in idea.entry_zone))
    stop, tp1, tp2 = price_fmt(idea.stop_loss), price_fmt(idea.take_profit_1), price_fmt(idea.take_profit_2)
    regime = str(idea.regime_type or idea.market_regime or "").upper()
    bearish = regime in {"TRENDING_DOWN", "BREAKDOWN", "TRANSITION_TO_BEARISH"}
    bullish = regime in {"TRENDING_UP", "BREAKOUT", "TRANSITION_TO_BULLISH"}
    ranging = regime in {"RANGE_BOUND", "CHOP"}
    if is_bounce:
        lead = "Counter-trend long."
    elif is_trend and ((is_long and bullish) or (not is_long and bearish)):
        lead = ("With-trend long: the broader structure favors buyers."
                if is_long else "With-trend short: the broader structure favors sellers.")
    elif is_trend and ranging:
        lead = ("Long setup in a ranging market; this is not a clear trend trade."
                if is_long else "Short setup in a ranging market; this is not a clear trend trade.")
    elif is_trend:
        lead = ("Counter-trend long: the setup is against the current downtrend."
                if is_long else "Counter-trend short: the setup is against the current uptrend.")
    else:
        lead = "Counter-trend setup: the trade is against the broader market direction."

    if bearish:
        trend_line = f"{coin} is in a downtrend on the {idea.timeframe.upper()}."
    elif bullish:
        trend_line = f"{coin} is in an uptrend on the {idea.timeframe.upper()}."
    elif ranging:
        trend_line = f"{coin} is ranging on the {idea.timeframe.upper()}."
    else:
        trend_line = ""

    trigger_sentences: list[str] = []
    volume_present = momentum_present = False
    btc_regime = ""
    stop_swing_level: float | None = None
    stop_atr_multiple: float | None = None
    target_basis = ""
    reason = str(idea.reason or "")
    reason_sentences = [part.strip() for part in reason.replace("\n", " ").split(". ") if part.strip()]
    for raw in reason_sentences:
        sentence = raw.rstrip(".")
        lower = sentence.lower()
        # Only these computed evidence templates can enter a public message.
        if lower.startswith((
            "price swept support at ", "price swept resistance at ",
            "after the reclaim, price held a higher low at ",
            "price rallied into resistance at ", "price retested broken support at ",
            "price broke below support at ", "price tested support at ",
            "price retested broken resistance at ", "price reclaimed resistance at ",
            "price is trading above the ", "price is trading below the ",
        )):
            trigger_sentences.append(sentence + ".")
        elif lower.startswith("the latest rally tested prior resistance between "):
            import re
            levels = re.search(r"between\s+([\d,.]+)\s+and\s+([\d,.]+)", sentence, re.IGNORECASE)
            if levels:
                zone_low, zone_high = (float(value.replace(",", "")) for value in levels.groups())
                entry_mid = sum(idea.entry_zone) / 2
                if entry_mid <= zone_high <= idea.stop_loss:
                    trigger_sentences.append(sentence + ".")
        elif lower.startswith("the latest pullback tested prior support between "):
            import re
            levels = re.search(r"between\s+([\d,.]+)\s+and\s+([\d,.]+)", sentence, re.IGNORECASE)
            if levels:
                zone_low, zone_high = (float(value.replace(",", "")) for value in levels.groups())
                entry_mid = sum(idea.entry_zone) / 2
                if idea.stop_loss <= zone_low <= entry_mid:
                    trigger_sentences.append(sentence + ".")
        elif lower.startswith("volume "):
            volume_present = True
        elif lower.startswith(("downside momentum remains present", "price momentum continues to favor buyers")):
            momentum_present = True
        elif lower.startswith("btc is in a downtrend"):
            btc_regime = "TRENDING_DOWN"
        elif lower.startswith("btc has broken below support"):
            btc_regime = "BREAKDOWN"
        elif lower.startswith("btc is in an uptrend"):
            btc_regime = "TRENDING_UP"
        elif lower.startswith("btc is breaking above resistance"):
            btc_regime = "BREAKOUT"
        elif lower.startswith("btc is ranging") or lower.startswith("btc is moving choppily"):
            btc_regime = "RANGE_BOUND"
        elif lower.startswith("btc is shifting toward a downtrend"):
            btc_regime = "TRANSITION_TO_BEARISH"
        elif lower.startswith("btc is shifting toward an uptrend"):
            btc_regime = "TRANSITION_TO_BULLISH"
        elif lower.startswith("stop anchor swing level: "):
            import re
            match = re.search(r"([\d,.]+)", sentence.split(":", 1)[1])
            if match:
                stop_swing_level = float(match.group(1).replace(",", ""))
        elif lower.startswith("stop distance: "):
            import re
            match = re.search(r"([\d,.]+)\s+atr\s+(above|below)\s+entry", sentence, re.IGNORECASE)
            if match:
                stop_atr_multiple = float(match.group(1).replace(",", ""))
        elif lower.startswith("target basis: "):
            basis = sentence.split(":", 1)[1].strip().rstrip(".").lower()
            target_basis = "4H EMA50" if basis == "4h ema50" else "resistance" if basis == "resistance" else ""

    # Bounce trigger text is a deterministic template with measured structure
    # fields. It is retained as two sentences in its trigger-story position.
    if is_bounce and reason_sentences:
        first = reason_sentences[0].rstrip(".")
        second = reason_sentences[1].rstrip(".") if len(reason_sentences) > 1 else ""
        if first.lower().startswith("price swept prior support at "):
            if second.lower().startswith("since then it held a higher low at "):
                second = "After the reclaim, price held" + second[len("Since then it held"):]
            trigger_sentences = [first + "."] + ([second + "."] if second.lower().startswith("after the reclaim, price held a higher low at ") else [])

    if is_trend and not trigger_sentences:
        structure = str(idea.regime_structure or "").lower()
        if "lower high" in structure or "lower low" in structure:
            trigger_sentences.append("Price structure is forming lower highs and lower lows.")
        elif "higher high" in structure or "higher low" in structure:
            trigger_sentences.append("Price structure is forming higher highs and higher lows.")
        elif bearish:
            trigger_sentences.append("The trend structure is bearish, with lower highs and lower lows.")
        elif bullish:
            trigger_sentences.append("The trend structure is bullish, with higher highs and higher lows.")
        elif ranging:
            trigger_sentences.append("Price is ranging without a confirmed directional trigger.")

    evidence_bits = []
    if volume_present:
        evidence_bits.append("volume supported the bearish move" if not is_long else "volume supported the bullish move")
    if momentum_present:
        evidence_bits.append("downside momentum remains present" if not is_long else "momentum continues to favor buyers")
    htf_text = {"HTF_BEARISH": "the higher-timeframe bias is bearish",
                "HTF_BULLISH": "the higher-timeframe bias is bullish",
                "HTF_NEUTRAL": "the higher-timeframe bias is neutral"}.get(str(idea.higher_timeframe_bias or ""))
    if htf_text and htf_text not in " ".join(evidence_bits) and not is_bounce:
        evidence_bits.append(htf_text)
    if len(evidence_bits) == 1:
        evidence_sentence = evidence_bits[0].capitalize() + "."
    elif len(evidence_bits) == 2:
        evidence_sentence = evidence_bits[0].capitalize() + " and " + evidence_bits[1] + "."
    elif len(evidence_bits) > 2:
        evidence_sentence = ", ".join(evidence_bits[:-1]).capitalize() + ", with " + evidence_bits[-1] + "."
    else:
        evidence_sentence = ""

    if isinstance(btc_context, dict):
        btc_regime = str(btc_context.get("btc_4h_regime") or btc_context.get("regime_type") or btc_context.get("regime") or btc_regime).upper()
    elif isinstance(btc_context, str) and btc_context:
        btc_regime = btc_context.upper()
    if btc_regime in {"BEAR", "BEARISH", "DOWNTREND"}:
        btc_regime = "TRENDING_DOWN"
    elif btc_regime in {"BULL", "BULLISH", "UPTREND"}:
        btc_regime = "TRENDING_UP"
    elif btc_regime in {"RANGING", "NEUTRAL"}:
        btc_regime = "RANGE_BOUND"
    btc_is_down = btc_regime in {"TRENDING_DOWN", "BREAKDOWN", "TRANSITION_TO_BEARISH"}
    btc_is_up = btc_regime in {"TRENDING_UP", "BREAKOUT", "TRANSITION_TO_BULLISH"}
    if btc_is_down:
        effect = "tailwind" if not is_long else "headwind"
        btc_sentence = f"BTC is in a downtrend on the 4H, a {effect} for this {'long' if is_long else 'short'}."
    elif btc_is_up:
        effect = "headwind" if not is_long else "tailwind"
        btc_sentence = f"BTC is in an uptrend on the 4H, a {effect} for this {'long' if is_long else 'short'}."
    elif btc_regime in {"RANGE_BOUND", "CHOP"}:
        btc_sentence = f"BTC is ranging on the 4H, leaving broad-market conditions neutral for this {'long' if is_long else 'short'}."
    else:
        btc_sentence = ""

    bounce_htf = {"HTF_BEARISH": "The higher-timeframe trend is bearish.",
                  "HTF_BULLISH": "The higher-timeframe trend is bullish.",
                  "HTF_NEUTRAL": "The higher-timeframe trend is neutral."}.get(str(idea.higher_timeframe_bias or ""), "")
    why = [lead, *( [bounce_htf] if is_bounce and bounce_htf else []), trend_line,
           *trigger_sentences, evidence_sentence, btc_sentence]
    narrative = " ".join(escape(part if " EMA" in part else _format_reason_prices(part, decimals), quote=False)
                         for part in why if part)
    if is_bounce and idea.entry_status == "WAIT_FOR_RETEST":
        # The canonical Telegram outbox intentionally does not dispatch this
        # state; this rendering remains accurate wherever a pending setup is shown.
        pending_stop_reason = (f"below swing low at {price_fmt(stop_swing_level)}"
                               if stop_swing_level is not None else "below the higher-low pivot")
        return (f"{color} {tag} · {safe_symbol} · {idea.timeframe.upper()}\n\n"
                f"{narrative}\n\nPending setup\nLimit entry: {entry}\nStop: {stop} ({pending_stop_reason})\n"
                "Valid for 6 candles. The alert is sent only after the retest fills.")
    entry_mid = sum(idea.entry_zone) / 2
    risk = abs(entry_mid - idea.stop_loss)
    rr1 = abs((idea.take_profit_1 - entry_mid) / risk) if risk else idea.risk_reward_ratio
    rr2 = abs((idea.take_profit_2 - entry_mid) / risk) if risk else idea.risk_reward_ratio
    side = "below" if is_long else "above"
    single_target = abs(float(idea.take_profit_1) - float(idea.take_profit_2)) < 1e-12
    if is_bounce:
        higher_low_match = next((__import__("re").search(r"higher low at ([\d,.]+)", sentence, __import__("re").IGNORECASE)
                                 for sentence in trigger_sentences if "higher low at" in sentence.lower()), None)
        stop_reason = f"below swing low at {price_fmt(float(higher_low_match.group(1).replace(',', '')))}" if higher_low_match else "below the higher-low pivot"
    elif stop_swing_level is not None:
        stop_reason = f"{'below' if is_long else 'above'} swing level {price_fmt(stop_swing_level)}"
    elif stop_atr_multiple is not None:
        stop_reason = f"{stop_atr_multiple:.1f} ATR {'below' if is_long else 'above'} entry"
    else:
        stop_reason = "below entry" if is_long else "above entry"
    validity = "Valid for 48 hours."
    if is_bounce:
        target_label = f" ({target_basis})" if target_basis else ""
        plan = (f"Plan\nEntry: {entry} (limit retest filled)\nStop: {stop} ({stop_reason})\nTarget: {tp1}{target_label}\n"
                f"Risk/reward: 1 : {rr1:.1f}\n\n{validity}")
    elif single_target:
        target_label = f" ({target_basis})" if target_basis else ""
        plan = (f"Plan\nEntry: {entry}\nStop: {stop} ({stop_reason})\nTarget: {tp1}{target_label}\n"
                f"Risk/reward: 1 : {rr1:.1f}\n\n{validity}")
    else:
        plan = (f"Plan\nEntry: {entry}\nStop: {stop} ({stop_reason})\nTP1: {tp1} (50%) · TP2: {tp2} (remaining 50%)\n"
                f"Risk/reward: 1 : {rr1:.1f} (TP1) · 1 : {rr2:.1f} (TP2)\n\n"
                f"{validity}")
    heading = (f"{color} {tag} · {safe_symbol} · {idea.timeframe.upper()}" if is_bounce or is_trend
               else f"{safe_symbol} · {idea.timeframe.upper()} · {tag}")
    return f"{heading}\n\n{narrative}\n\n{plan}"

def format_outcome_event(event: dict) -> str:
    from decimal import Decimal
    from html import escape
    labels={"TP1":"TP1 hit — partial taken (50%).","TP2":"TP2 hit — final target reached.","STOP_LOSS":"Stop-loss hit.","TIME_EXIT":"48-hour tracking window ended — trade closed."}
    decimals = event.get("price_decimals")
    price = (f"{float(event['price']):,.{int(decimals)}f}" if decimals is not None else
             (format(Decimal(str(event["price"])),"f").rstrip("0").rstrip(".") if "." in str(event["price"]) else str(event["price"])))
    entry = (float(event.get("entry_zone_low") or 0) + float(event.get("entry_zone_high") or 0)) / 2
    stop = float(event.get("stop_loss") or 0)
    risk = abs(entry - stop)
    direction = str(event.get("direction") or "LONG").upper()
    sign = 1 if direction == "LONG" else -1
    trade_r = max(0.0, (float(event["price"]) - entry) * sign / risk) if risk else 0.0
    quantity = float(event.get("quantity") or 1)
    gained = trade_r * quantity
    symbol = escape(alert_symbol(event["symbol"]), quote=False)
    decimals = event.get("price_decimals")
    price_fmt = lambda value: f"{value:,.{int(decimals)}f}" if decimals is not None else fmt(value)
    if event.get("setup_family") == "bounce_long" and event["event_kind"] in {"TP1", "TP2"}:
        label = "TP1" if event["event_kind"] == "TP1" else "TP2"
        tag = f"🔵 Bounce long {symbol} hit {label} ✅"
    elif event.get("setup_family") == "trend_continuation" and event["event_kind"] in {"TP1", "TP2"}:
        label = "TP1" if event["event_kind"] == "TP1" else "TP2"
        side = str(event.get("direction") or "").upper()
        tag = f"{'🟢 Trend long' if side == 'LONG' else '🔴 Trend short'} {symbol} hit {label} ✅"
    else:
        return f"{symbol} · {escape(str(event['direction']), quote=False)}\n{labels[event['event_kind']]}\nPrice: {price}"
    if quantity < 0.999:
        portion = "half the position" if abs(quantity - 0.5) < 1e-9 else f"{quantity * 100:.0f}% of the position"
        from app.config import get_settings
        if get_settings().trade_history_move_stop_to_entry_after_tp1:
            stop_rule = f"After TP1, the stop moves to entry at {price_fmt(entry)}."
        else:
            stop_rule = f"The stop stays at its original level, {price_fmt(stop)}."
        return f"{tag} +{trade_r:.1f}R on {portion} (+{gained:.1f}R banked). {stop_rule}\nPrice: {price_fmt(float(event['price']))}"
    return f"{tag} +{gained:.1f}R\nPrice: {price_fmt(float(event['price']))}"


def format_forex_alert(signal: dict) -> str:
    return (
        "SwiftChart Forex Signal\n\n"
        "Market: FOREX\n"
        f"Pair: {signal.get('pair', '-')}\n"
        f"Direction: {signal.get('direction', '-')}\n"
        f"Session: {signal.get('session', '-')}\n"
        f"Bias: {signal.get('pre_session_bias', '-')}\n"
        f"Entry: {fmt(signal.get('entry'))}\n"
        f"Stop Loss: {fmt(signal.get('stopLoss'))}\n"
        f"TP1: {fmt(signal.get('tp1'))}\n"
        f"TP2: {fmt(signal.get('tp2'))}\n"
        f"R:R: {fmt(signal.get('rr'))}\n"
        f"News Risk: {str(signal.get('newsRisk', '-')).title()}\n"
        f"Spread: {str(signal.get('spreadStatus', '-')).title()}\n"
        f"Reason: {signal.get('reason', '-')}\n\n"
        f"{RISK_WARNING}"
    )


def format_history(records: list[dict]) -> str:
    if not records:
        return "SwiftChart History\n\nNo saved trade ideas yet."

    lines = ["SwiftChart History — Latest 5"]
    for record in records[:5]:
        lines.append(
            "\n"
            f"{record['symbol']} — {record['timeframe'].upper()} — {record['direction']} ({record.get('exchange', '-')})\n"
            f"Status: {record['status']}\n"
            f"TP/SL: TP1 {fmt(record['take_profit_1'])} | TP2 {fmt(record['take_profit_2'])} | SL {fmt(record['stop_loss'])}"
        )
    lines.append(f"\n{RISK_WARNING}")
    return "\n".join(lines)



def format_stats(_data: dict) -> str:
    """Keep legacy command/button entry points inert without fetching metrics."""
    return "Performance statistics are unavailable."


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
            f"Status: {record.get('status', '-')}\n"
            f"Opened: {record.get('opened_at') or '-'}\n"
            f"Closed: {record.get('closed_at') or '-'}"
        )
    lines.append("\nSimulated trades only. No real orders are placed.")
    return "\n".join(lines)


def strategy_text() -> str:
    return (
        "SwiftChart Strategy\n\n"
        "SwiftChart classifies market regime first, identifies support/resistance zones, confirms liquidity sweeps, "
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
