import asyncio
import logging
import os
from time import monotonic

from telegram import Bot
from telegram.error import Forbidden

from app.config import get_settings

from app.models.schemas import TradeIdea
from app.services.alert_dedupe import mark_alert_sent as mark_dedupe_sent
from app.services.alert_dedupe import should_skip_canonical_alert
from app.services.execution_signals import execution_signal_id
from app.services.telegram_dispatch import (
    DISPATCH_SUCCEEDED,
    DISPATCH_PARTIAL_FINAL,
    claim_telegram_dispatches,
    mark_telegram_recipient_failure,
    mark_telegram_recipient_success,
    suppress_telegram_dispatch_for_dedupe,
    permanent_telegram_failure_reason,
    recipient_is_claimed,
)
from bot.formatter import format_trade_alert
from bot.scanner import _btc_context
from bot.keyboards import trade_alert_keyboard
from bot.storage import get_subscribers, save_signal
from app.forex.telegram import dispatch_pending_forex, dispatch_pending_forex_limits

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


async def dispatch_crypto_outbox(bot: Bot) -> dict[str, int | str]:
    """Drain the existing canonical outbox; never scan markets or poll Forex."""
    subscribers = _crypto_subscribers()
    candidates = claim_telegram_dispatches(subscribers)
    btc_contexts = {}
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
        if idea.exchange not in btc_contexts:
            try:
                btc_contexts[idea.exchange] = await asyncio.wait_for(_btc_context(idea.exchange), timeout=5)
            except Exception:
                logger.warning("BTC alert context unavailable exchange=%s", idea.exchange)
                btc_contexts[idea.exchange] = None
        message = format_trade_alert(idea, btc_context=btc_contexts[idea.exchange])
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
            if not recipient_is_claimed(candidate.dispatch_id, chat_id):
                continue
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
                    now=getattr(response, "date", None),
                )
            except Exception as exc:
                failed += 1
                reason = "FORBIDDEN" if isinstance(exc, Forbidden) else permanent_telegram_failure_reason(f"{type(exc).__name__}: {exc}")
                retry_after = getattr(exc, "retry_after", 0)
                if hasattr(retry_after, "total_seconds"):
                    retry_after = retry_after.total_seconds()
                final_status = mark_telegram_recipient_failure(
                    candidate.dispatch_id, chat_id, f"{type(exc).__name__}: delivery failed",
                    permanent_reason=reason, retry_after_seconds=float(retry_after or 0),
                )
                logger.warning("Canonical crypto delivery failed dispatch_id=%s error_type=%s permanent=%s",
                               candidate.dispatch_id, type(exc).__name__, bool(reason))
        if final_status in (DISPATCH_SUCCEEDED, DISPATCH_PARTIAL_FINAL):
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


async def run_alert_scan(bot: Bot) -> dict:
    """Keep the existing manual combined entry point; scheduled crypto is separate."""
    forex_dispatch = await dispatch_pending_forex(bot)
    forex_limit_dispatch = await dispatch_pending_forex_limits(bot)
    result = await dispatch_crypto_outbox(bot)
    return {**result,"forex_dispatch":forex_dispatch,"forex_limit_dispatch":forex_limit_dispatch}


async def alert_loop(bot: Bot) -> None:
    interval = get_settings().crypto_telegram_dispatch_interval_seconds
    await asyncio.sleep(int(os.getenv("ALERT_STARTUP_DELAY_SECONDS", "20")))
    while True:
        started = monotonic()
        try:
            result = await dispatch_crypto_outbox(bot)
            logger.info("Canonical Telegram dispatch poll complete: %s", result)
        except Exception:
            logger.exception("Canonical Telegram dispatch poll failed")
        await asyncio.sleep(max(0.1, interval-(monotonic()-started)))


async def forex_alert_loop(bot: Bot) -> None:
    interval = int(os.getenv("FOREX_TELEGRAM_DISPATCH_INTERVAL_SECONDS", "60"))
    await asyncio.sleep(int(os.getenv("FOREX_TELEGRAM_DISPATCH_STARTUP_DELAY_SECONDS", "10")))
    while True:
        try:
            result = await dispatch_pending_forex(bot)
            limit_result = await dispatch_pending_forex_limits(bot)
            logger.info("Forex Telegram dispatch complete: signals=%s limits=%s", result, limit_result)
        except Exception:
            logger.exception("Forex Telegram dispatch failed")
        await asyncio.sleep(max(30, interval))
