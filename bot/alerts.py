import asyncio
import logging
import os
from collections import Counter

from telegram import Bot

from app.config import get_settings
from app.models.schemas import TradeIdea
from app.services.alert_dedupe import mark_alert_sent as mark_dedupe_sent
from app.services.alert_dedupe import setup_fingerprint, should_skip_alert
from bot.formatter import format_trade_alert
from bot.scanner import scan_top_ideas
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
    rejection_reasons: Counter[str] = Counter()
    subscribers = get_subscribers()
    if not subscribers:
        rejection_reasons["missing subscribers"] += 1
        logger.info(
            "telegram_alert_scan_complete subscribers=0 symbols_scanned=0 valid_ideas_found=0 eligible_alerts=0 sent_alerts=0 rejection_reasons=%s",
            dict(rejection_reasons),
        )
        return {"status": "ok", "subscribers": 0, "ideas": 0, "eligible": 0, "sent": 0, "rejection_reasons": dict(rejection_reasons)}

    ideas, selected_exchange, scan_meta = await scan_top_ideas(timeframe, exchange)
    rejection_reasons.update(scan_meta.get("rejection_reasons", {}))
    eligible_ideas = []
    ready_ideas = 0
    score_eligible_ideas = 0
    skipped_by_score = 0
    skipped_by_entry_status = 0
    for idea in ideas:
        score_ok = idea_score(idea) >= min_score
        ready = idea.entry_status == "READY"
        if score_ok:
            score_eligible_ideas += 1
        if ready:
            ready_ideas += 1
        if not score_ok:
            skipped_by_score += 1
            rejection_reasons["score below ALERT_MIN_SCORE"] += 1
            continue
        if not ready:
            skipped_by_entry_status += 1
            rejection_reasons["entry_status not READY"] += 1
            continue
        eligible_ideas.append(idea)
    sent = 0
    skipped_by_dedup = 0
    for idea in eligible_ideas:
        key = alert_key(idea)
        if is_alert_sent(key) or should_skip_alert(idea, namespace="telegram"):
            skipped_by_dedup += 1
            rejection_reasons["duplicate alert"] += 1
            continue
        message = format_trade_alert(idea)
        for chat_id in subscribers:
            try:
                await bot.send_message(chat_id=chat_id, text=message)
                sent += 1
            except Exception as exc:
                rejection_reasons["send error"] += 1
                logger.warning("Could not send alert to chat %s: %s", chat_id, exc)
        mark_alert_sent(key)
        mark_dedupe_sent(idea, namespace="telegram")

    logger.info(
        (
            "telegram_alert_scan_complete exchange=%s timeframe=%s symbols_scanned=%s valid_ideas_found=%s "
            "ready_ideas=%s score_eligible_ideas=%s eligible_alerts=%s sent_alerts=%s "
            "skipped_by_dedup=%s skipped_by_score=%s skipped_by_entry_status=%s rejection_reasons=%s"
        ),
        selected_exchange,
        timeframe,
        scan_meta.get("symbols_scanned", 0),
        scan_meta.get("valid_ideas_found", len(ideas)),
        ready_ideas,
        score_eligible_ideas,
        len(eligible_ideas),
        sent,
        skipped_by_dedup,
        skipped_by_score,
        skipped_by_entry_status,
        dict(rejection_reasons),
    )
    return {
        "status": "ok",
        "exchange": selected_exchange,
        "timeframe": timeframe,
        "subscribers": len(subscribers),
        "ideas": len(ideas),
        "symbols_scanned": scan_meta.get("symbols_scanned", 0),
        "valid_ideas_found": scan_meta.get("valid_ideas_found", len(ideas)),
        "ready_ideas": ready_ideas,
        "score_eligible_ideas": score_eligible_ideas,
        "eligible": len(eligible_ideas),
        "min_score": min_score,
        "sent": sent,
        "skipped_by_dedup": skipped_by_dedup,
        "skipped_by_score": skipped_by_score,
        "skipped_by_entry_status": skipped_by_entry_status,
        "rejection_reasons": dict(rejection_reasons),
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
