from __future__ import annotations

import html
import json
import logging
import os
from pathlib import Path

from app.forex.config import SUPPORTED_FOREX_PAIRS
from app.forex.models import ForexLimitOpportunity, ForexSignalPlan
from app.forex.storage import (
    claim_pending_dispatches,
    get_signal,
    list_signals,
    mark_dispatch_attempt,
    queue_dispatches,
)
from app.forex.limit_storage import (
    claim_limit_dispatches,
    get_limit_opportunity,
    mark_limit_dispatch,
    queue_limit_dispatches,
)

logger = logging.getLogger(__name__)
TELEGRAM_FOREX_TIMEFRAMES = frozenset({"1H", "4H", "1D"})


def _telegram_subscribers() -> list[str]:
    subscribers: set[str] = set()
    for value in os.getenv("TELEGRAM_ALERT_CHAT_IDS", "").split(","):
        if value.strip():
            subscribers.add(str(int(value.strip())))
    state_path = Path(os.getenv("BOT_STATE_PATH", ".swiftchart_bot_state.json"))
    if state_path.exists():
        payload = json.loads(state_path.read_text())
        subscribers.update(str(int(chat_id)) for chat_id in payload.get("subscribers", []))
    return sorted(subscribers)


def _price(value: float, symbol: str) -> str:
    pair = SUPPORTED_FOREX_PAIRS.get(symbol.upper())
    precision = 3 if pair and pair.pip_size >= 0.01 else 5
    return f"{value:.{precision}f}"


def _application_url(app_url: str | None = None) -> str:
    return (app_url or os.getenv("APP_BASE_URL") or "https://swiftchart.xyz").rstrip("/")


def format_forex_signal(signal: ForexSignalPlan, app_url: str | None = None) -> str:
    direction = "LONG" if signal.direction == "LONG" else "SHORT"
    detail_url = f"{_application_url(app_url)}/app/signal/{signal.id}"
    if signal.entry_low == signal.entry_high:
        entry = _price(signal.entry_low, signal.symbol)
    else:
        entry = (
            f"{_price(signal.entry_low, signal.symbol)} - "
            f"{_price(signal.entry_high, signal.symbol)}"
        )
    return "\n".join(
        [
            f"<b>{html.escape(signal.symbol)} {direction}</b>",
            "",
            f"Entry: {entry}",
            f"Stop: {_price(signal.stop_loss, signal.symbol)}",
            f"TP1: {_price(signal.take_profit_1, signal.symbol)}",
            f"TP2: {_price(signal.take_profit_2, signal.symbol)}",
            f"Timeframe: {signal.timeframe}",
            f"Bias: {html.escape(signal.bias.upper())}",
            f"Score: {signal.setup_score:g}",
            "",
            f'<a href="{html.escape(detail_url)}">Open signal</a>',
        ]
    )


def format_forex_limit_opportunity(opportunity: ForexLimitOpportunity, app_url: str | None = None) -> str:
    context = opportunity.context
    lines = [
        "🟡 <b>FOREX LIMIT OPPORTUNITY</b>",
        "",
        f"<b>{html.escape(opportunity.pair)} — {opportunity.order_type.replace('_', ' ')}</b>",
        "Strategy: Liquidity Sweep + FVG",
        f"Timeframe: {opportunity.timeframe}",
        "",
        f"Entry: {_price(opportunity.entry_price, opportunity.pair)}",
        f"Entry Zone: {_price(opportunity.entry_zone_low, opportunity.pair)} – {_price(opportunity.entry_zone_high, opportunity.pair)}",
        f"Stop Loss: {_price(opportunity.stop_loss, opportunity.pair)}",
        f"TP1: {_price(opportunity.take_profit_1, opportunity.pair)}",
        f"TP2: {_price(opportunity.take_profit_2, opportunity.pair)}",
        "",
        f"RR to TP1: {opportunity.risk_reward_1:g}R",
        f"RR to TP2: {opportunity.risk_reward_2:g}R",
        f"Expires: After {opportunity.expiry_candle_count} completed {opportunity.timeframe} candles",
        "",
        "Setup:",
        *[html.escape(reason) for reason in opportunity.reasoning],
    ]
    if context.usd_context:
        lines.append(f"DXY: {context.usd_context.state.replace('_', ' ').title()} — {context.usd_context.alignment_status.replace('_', ' ').title()}")
    if context.oil_context:
        lines.append(f"Oil: {context.oil_context.state.replace('_', ' ').title()} — {context.oil_context.alignment_status.replace('_', ' ').title()}")
    lines.extend(
        [
            f"Context Adjustment: {context.total_adjustment:+g}",
            f"Final Score: {opportunity.final_score:g}",
            "",
            f"Status: WAITING FOR {opportunity.order_type.replace('_', ' ')}",
            "This is not an active trade until entry is filled.",
            "",
            f'<a href="{html.escape(_application_url(app_url))}/app/forex/limits/{opportunity.id}">View Limit Opportunity</a>',
        ]
    )
    return "\n".join(lines)


def format_limit_lifecycle_event(opportunity: ForexLimitOpportunity) -> str:
    if opportunity.opportunity_status == "ACTIVE_TRADE":
        return "\n".join([
            "🟢 <b>LIMIT ORDER FILLED</b>", "",
            f"{opportunity.pair} — {opportunity.direction} ACTIVE",
            f"Entry: {_price(opportunity.entry_price, opportunity.pair)}",
            f"Stop: {_price(opportunity.stop_loss, opportunity.pair)}",
            f"TP1: {_price(opportunity.take_profit_1, opportunity.pair)}",
            f"TP2: {_price(opportunity.take_profit_2, opportunity.pair)}",
        ])
    if opportunity.opportunity_status == "EXPIRED":
        return "\n".join([
            "⚪ <b>LIMIT OPPORTUNITY EXPIRED</b>", "",
            f"{opportunity.pair} {opportunity.order_type.replace('_', ' ')} was not filled before expiry.",
            "No trade was opened.",
        ])
    return f"{opportunity.pair} limit opportunity: {opportunity.opportunity_status.replace('_', ' ').title()}"


def enqueue_forex_limit_event(opportunity: ForexLimitOpportunity, event_type: str) -> int:
    try:
        subscribers = _telegram_subscribers()
    except Exception:
        logger.exception("Could not load limit-opportunity Telegram subscribers")
        return 0
    return queue_limit_dispatches(opportunity.id, event_type, subscribers)


async def dispatch_pending_forex_limits(bot, *, app_url: str | None = None) -> dict[str, int]:
    attempted = delivered = failed = 0
    for dispatch in claim_limit_dispatches():
        opportunity = get_limit_opportunity(dispatch["opportunity_id"])
        if opportunity is None:
            mark_limit_dispatch(dispatch["id"], delivered=False, error="Opportunity not found.")
            failed += 1
            continue
        attempted += 1
        text = (
            format_forex_limit_opportunity(opportunity, app_url)
            if dispatch["event_type"] == "OPPORTUNITY"
            else format_limit_lifecycle_event(opportunity)
        )
        try:
            await bot.send_message(
                chat_id=dispatch["chat_id"], text=text, parse_mode="HTML",
                disable_web_page_preview=True,
            )
            mark_limit_dispatch(dispatch["id"], delivered=True)
            delivered += 1
        except Exception as exc:
            mark_limit_dispatch(dispatch["id"], delivered=False, error=f"{type(exc).__name__}: {str(exc)[:240]}")
            failed += 1
    return {"attempted": attempted, "delivered": delivered, "failed": failed}


def enqueue_forex_signal(signal: ForexSignalPlan) -> int:
    if signal.timeframe not in TELEGRAM_FOREX_TIMEFRAMES:
        logger.info(
            "Forex Telegram skipped website-only timeframe signal_id=%s timeframe=%s",
            signal.id,
            signal.timeframe,
        )
        return 0
    try:
        subscribers = _telegram_subscribers()
    except Exception:
        logger.exception("Could not load Forex Telegram subscribers signal_id=%s", signal.id)
        return 0
    return queue_dispatches(signal.id, subscribers)


def reconcile_active_forex_dispatches() -> int:
    """Repair outbox gaps caused by a transient subscriber-store failure."""
    try:
        subscribers = _telegram_subscribers()
    except Exception:
        logger.exception("Could not reconcile Forex Telegram subscribers")
        return 0
    if not subscribers:
        return 0
    queued = 0
    for signal in list_signals(statuses=("PENDING_ENTRY", "OPEN", "TP1_HIT_TP2_RUNNING"), limit=200):
        if not signal.is_legacy and signal.timeframe in TELEGRAM_FOREX_TIMEFRAMES:
            queued += queue_dispatches(signal.id, subscribers)
    return queued


async def dispatch_pending_forex(bot, *, app_url: str | None = None) -> dict[str, int]:
    reconcile_active_forex_dispatches()
    attempted = delivered = failed = 0
    for dispatch in claim_pending_dispatches():
        signal = get_signal(dispatch["signal_id"])
        if signal is None:
            mark_dispatch_attempt(
                dispatch["id"],
                delivered=False,
                error_message="Signal no longer exists.",
            )
            failed += 1
            continue
        if signal.timeframe not in TELEGRAM_FOREX_TIMEFRAMES:
            mark_dispatch_attempt(dispatch["id"], delivered=True)
            logger.info(
                "Forex Telegram suppressed queued website-only signal_id=%s timeframe=%s",
                signal.id,
                signal.timeframe,
            )
            continue
        attempted += 1
        try:
            await bot.send_message(
                chat_id=dispatch["chat_id"],
                text=format_forex_signal(signal, app_url),
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
            mark_dispatch_attempt(dispatch["id"], delivered=True)
            delivered += 1
        except Exception as exc:
            mark_dispatch_attempt(
                dispatch["id"],
                delivered=False,
                error_message=f"{type(exc).__name__}: {str(exc)[:240]}",
            )
            failed += 1
            logger.warning(
                "Forex Telegram delivery failed signal_id=%s chat_id=%s error=%s",
                signal.id,
                dispatch["chat_id"],
                type(exc).__name__,
            )
    return {"attempted": attempted, "delivered": delivered, "failed": failed}
