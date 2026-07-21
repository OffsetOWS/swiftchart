import asyncio
import logging
import os

from telegram import Bot

from app.models.schemas import TradeIdea
from app.services.alert_dedupe import mark_alert_sent as mark_dedupe_sent
from app.services.alert_dedupe import should_skip_canonical_alert
from app.services.execution_signals import execution_signal_id
from app.services.telegram_dispatch import (
    DISPATCH_SUCCEEDED,
    claim_telegram_dispatches,
    mark_telegram_recipient_failure,
    mark_telegram_recipient_success,
    suppress_telegram_dispatch_for_dedupe,
)
from bot.formatter import format_trade_alert
from bot.keyboards import trade_alert_keyboard
from bot.storage import get_subscribers, save_signal

logger = logging.getLogger(__name__)


def is_limit_order_alertable(idea: TradeIdea) -> bool:
    return bool(
        idea.strategy_version
        and idea.opportunity_key
        and idea.strategy_decision == "TRADE"
        and idea.entry_status == "READY"
        and idea.executable_at is not None
    )


def _crypto_subscribers() -> set[int]:
    """Use production market preferences when present without coupling to Forex code."""
    try:
        return {int(chat_id) for chat_id in get_subscribers("crypto")}
    except TypeError:
        return {int(chat_id) for chat_id in get_subscribers()}


async def run_alert_scan(bot: Bot) -> dict[str, int | str]:
    subscribers = _crypto_subscribers()
    candidates = claim_telegram_dispatches(subscribers)
    sent = 0
    failed = 0
    skipped_by_dedup = 0
    recipients_pending = sum(len(candidate.recipient_chat_ids) for candidate in candidates)
    for candidate in candidates:
        idea = candidate.idea
        if not is_limit_order_alertable(idea):
            logger.error(
                "canonical_dispatch_rejected trade_idea_id=%s opportunity_key=%s decision=%s entry_status=%s executable_at=%s",
                candidate.trade_idea_id,
                candidate.opportunity_key,
                idea.strategy_decision,
                idea.entry_status,
                idea.executable_at,
            )
            continue
        if candidate.first_attempt and should_skip_canonical_alert(idea, namespace="telegram"):
            skipped_by_dedup += 1
            suppress_telegram_dispatch_for_dedupe(
                candidate.dispatch_id,
                "Canonical opportunity already exists in the legacy Telegram final-safety dedupe state.",
            )
            continue
        message = format_trade_alert(idea)
        signal_id = execution_signal_id(idea)
        entry = sum(idea.entry_zone) / 2
        save_signal(
            signal_id,
            {
                "signal_id": signal_id,
                "pair": idea.symbol.upper(),
                "side": idea.direction.lower(),
                "entry": entry,
                "stop_loss": idea.stop_loss,
                "tp1": idea.take_profit_1,
                "tp2": idea.take_profit_2,
                "exchange": idea.exchange,
                "timeframe": idea.timeframe,
                "analysis": message,
            },
        )
        final_status = None
        for chat_id in candidate.recipient_chat_ids:
            try:
                response = await bot.send_message(
                    chat_id=chat_id,
                    text=message,
                    reply_markup=trade_alert_keyboard(signal_id),
                )
                sent += 1
                final_status = mark_telegram_recipient_success(
                    candidate.dispatch_id,
                    chat_id,
                    telegram_message_id=getattr(response, "message_id", None),
                )
            except Exception as exc:
                failed += 1
                final_status = mark_telegram_recipient_failure(
                    candidate.dispatch_id,
                    chat_id,
                    f"{type(exc).__name__}: {exc}",
                )
                logger.warning("Could not send alert to chat %s: %s", chat_id, exc)
        if final_status == DISPATCH_SUCCEEDED:
            mark_dedupe_sent(idea, namespace="telegram")

    logger.info(
        (
            "telegram_canonical_dispatch_complete subscribers=%s opportunities=%s recipients_pending=%s "
            "recipient_deliveries=%s failures=%s skipped_by_dedup=%s"
        ),
        len(subscribers),
        len(candidates),
        recipients_pending,
        sent,
        failed,
        skipped_by_dedup,
    )
    return {
        "status": "ok",
        "source": "canonical_persisted_v2_opportunities",
        "subscribers": len(subscribers),
        "ideas": len(candidates),
        "eligible": len(candidates),
        "recipients_pending": recipients_pending,
        "sent": sent,
        "alerts_sent": sent,
        "failed": failed,
        "skipped_by_dedup": skipped_by_dedup,
    }


async def alert_loop(bot: Bot) -> None:
    interval = int(os.getenv("ALERT_SCAN_INTERVAL_SECONDS", "1800"))
    await asyncio.sleep(int(os.getenv("ALERT_STARTUP_DELAY_SECONDS", "20")))
    while True:
        try:
            result = await run_alert_scan(bot)
            logger.info("Canonical Telegram dispatch poll complete: %s", result)
        except Exception:
            logger.exception("Canonical Telegram dispatch poll failed")
        await asyncio.sleep(max(300, interval))
