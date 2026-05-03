import asyncio
import logging
import os

from telegram import Bot

from app.config import get_settings
from app.models.schemas import TradeIdea
from bot.formatter import format_trade_alert
from bot.handlers import scan_top_ideas
from bot.storage import get_subscribers, is_alert_sent, mark_alert_sent

logger = logging.getLogger(__name__)


def alert_key(idea: TradeIdea) -> str:
    entry_low, entry_high = idea.entry_zone
    return "|".join(
        [
            idea.symbol,
            idea.timeframe,
            idea.direction,
            str(round(entry_low, 4)),
            str(round(entry_high, 4)),
            str(round(idea.stop_loss, 4)),
            str(round(idea.take_profit_2, 4)),
            str(round(idea.setup_score or idea.confidence_score, 1)),
        ]
    )


async def run_alert_scan(bot: Bot) -> dict[str, int | str]:
    timeframe = os.getenv("ALERT_TIMEFRAME", get_settings().default_timeframe)
    exchange = os.getenv("ALERT_EXCHANGE", get_settings().default_exchange)
    subscribers = get_subscribers()
    if not subscribers:
        return {"status": "ok", "subscribers": 0, "ideas": 0, "sent": 0}

    ideas, selected_exchange = await scan_top_ideas(timeframe, exchange)
    sent = 0
    for idea in ideas:
        key = alert_key(idea)
        if is_alert_sent(key):
            continue
        message = format_trade_alert(idea)
        for chat_id in subscribers:
            try:
                await bot.send_message(chat_id=chat_id, text=message)
                sent += 1
            except Exception as exc:
                logger.warning("Could not send alert to chat %s: %s", chat_id, exc)
        mark_alert_sent(key)

    return {
        "status": "ok",
        "exchange": selected_exchange,
        "timeframe": timeframe,
        "subscribers": len(subscribers),
        "ideas": len(ideas),
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
