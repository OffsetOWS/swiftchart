import asyncio
import logging
import os

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


async def run_alert_scan(bot: Bot) -> dict[str, int | str]:
    timeframe = os.getenv("ALERT_TIMEFRAME", get_settings().default_timeframe)
    exchange = os.getenv("ALERT_EXCHANGE", get_settings().default_exchange)
    min_score = alert_min_score()
    subscribers = get_subscribers()
    if not subscribers:
        return {"status": "ok", "subscribers": 0, "ideas": 0, "sent": 0}

    ideas, selected_exchange = await scan_top_ideas(timeframe, exchange)
    eligible_ideas = [idea for idea in ideas if idea_score(idea) >= min_score and idea.entry_status == "READY"]
    sent = 0
    for idea in eligible_ideas:
        key = alert_key(idea)
        if is_alert_sent(key) or should_skip_alert(idea, namespace="telegram"):
            continue
        message = format_trade_alert(idea)
        for chat_id in subscribers:
            try:
                await bot.send_message(chat_id=chat_id, text=message)
                sent += 1
            except Exception as exc:
                logger.warning("Could not send alert to chat %s: %s", chat_id, exc)
        mark_alert_sent(key)
        mark_dedupe_sent(idea, namespace="telegram")

    return {
        "status": "ok",
        "exchange": selected_exchange,
        "timeframe": timeframe,
        "subscribers": len(subscribers),
        "ideas": len(ideas),
        "eligible": len(eligible_ideas),
        "min_score": min_score,
        "sent": sent,
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
