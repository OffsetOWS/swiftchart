import asyncio
import logging
import os
from typing import Any

from telegram import Bot

from app.config import get_settings
from app.models.schemas import TradeIdea
from app.services.alert_dedupe import mark_alert_sent as mark_dedupe_sent
from app.services.alert_dedupe import setup_fingerprint, should_skip_alert
from bot.formatter import format_trade_alert
from bot.handlers import scan_top_ideas
from bot.storage import get_subscribers, is_alert_sent, mark_alert_sent

logger = logging.getLogger(__name__)


def alert_min_score() -> float:
    return float(os.getenv("ALERT_MIN_SCORE", "75"))


def idea_score(idea: TradeIdea) -> float:
    return float(idea.setup_score or idea.confidence_score or 0)


def alert_key(idea: TradeIdea) -> str:
    return setup_fingerprint(idea)


def alert_eligibility_reason(idea: TradeIdea, min_score: float) -> str:
    score = idea_score(idea)
    if score < min_score:
        return "score_below_min"
    if idea.entry_status != "READY":
        return f"entry_status_{idea.entry_status.lower()}"
    return "eligible"


async def run_alert_scan(bot: Bot) -> dict[str, Any]:
    timeframe = os.getenv("ALERT_TIMEFRAME", get_settings().default_timeframe)
    exchange = os.getenv("ALERT_EXCHANGE", get_settings().default_exchange)
    min_score = alert_min_score()
    subscribers = get_subscribers()
    logger.info(
        "Telegram alert scan started exchange=%s timeframe=%s subscribers=%s min_score=%s",
        exchange,
        timeframe,
        len(subscribers),
        min_score,
    )
    if not subscribers:
        logger.info("Telegram alert scan skipped: no subscribers")
        return {"status": "ok", "subscribers": 0, "ideas": 0, "eligible": 0, "sent": 0}

    ideas, selected_exchange = await scan_top_ideas(timeframe, exchange)
    rejection_reasons: dict[str, int] = {}
    eligible_ideas = []
    for idea in ideas:
        reason = alert_eligibility_reason(idea, min_score)
        if reason == "eligible":
            eligible_ideas.append(idea)
        else:
            rejection_reasons[reason] = rejection_reasons.get(reason, 0) + 1

    sent = 0
    failed = 0
    duplicate_bot_state = 0
    duplicate_dedupe = 0
    for idea in eligible_ideas:
        key = alert_key(idea)
        if is_alert_sent(key):
            duplicate_bot_state += 1
            logger.info(
                "Telegram alert skipped duplicate bot state symbol=%s timeframe=%s direction=%s score=%s status=%s",
                idea.symbol,
                idea.timeframe,
                idea.direction,
                idea_score(idea),
                idea.entry_status,
            )
            continue
        if should_skip_alert(idea, namespace="telegram"):
            duplicate_dedupe += 1
            logger.info(
                "Telegram alert skipped duplicate dedupe symbol=%s timeframe=%s direction=%s score=%s status=%s",
                idea.symbol,
                idea.timeframe,
                idea.direction,
                idea_score(idea),
                idea.entry_status,
            )
            continue
        message = format_trade_alert(idea)
        delivered = 0
        for chat_id in subscribers:
            try:
                await bot.send_message(chat_id=chat_id, text=message)
                delivered += 1
                sent += 1
            except Exception as exc:
                failed += 1
                logger.warning("Could not send alert to chat %s: %s", chat_id, exc)
        if delivered:
            mark_alert_sent(key)
            mark_dedupe_sent(idea, namespace="telegram")
            logger.info(
                "Telegram alert sent symbol=%s timeframe=%s direction=%s score=%s status=%s delivered=%s failed=%s",
                idea.symbol,
                idea.timeframe,
                idea.direction,
                idea_score(idea),
                idea.entry_status,
                delivered,
                len(subscribers) - delivered,
            )
        else:
            logger.warning(
                "Telegram alert not marked sent because all sends failed symbol=%s timeframe=%s direction=%s subscribers=%s",
                idea.symbol,
                idea.timeframe,
                idea.direction,
                len(subscribers),
            )

    return {
        "status": "ok",
        "exchange": selected_exchange,
        "timeframe": timeframe,
        "subscribers": len(subscribers),
        "ideas": len(ideas),
        "eligible": len(eligible_ideas),
        "rejected": len(ideas) - len(eligible_ideas),
        "rejection_reasons": rejection_reasons,
        "duplicate_bot_state": duplicate_bot_state,
        "duplicate_dedupe": duplicate_dedupe,
        "min_score": min_score,
        "sent": sent,
        "failed": failed,
    }


async def alert_loop(bot: Bot) -> None:
    interval = int(os.getenv("ALERT_SCAN_INTERVAL_SECONDS", "1800"))
    await asyncio.sleep(int(os.getenv("ALERT_STARTUP_DELAY_SECONDS", "20")))
    while True:
        try:
            result = await run_alert_scan(bot)
            logger.info("Alert scan complete: %s", result)
        except Exception:
            logger.exception("Alert scan failed")
        await asyncio.sleep(max(300, interval))
