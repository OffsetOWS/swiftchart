import asyncio
import logging
import os
from collections import Counter

from telegram import Bot

from app.config import get_settings
from app.models.schemas import TradeIdea
from app.services.alert_dedupe import mark_alert_sent as mark_dedupe_sent
from app.services.alert_dedupe import should_skip_alert
from app.services.execution_signals import execution_signal_id
from bot.formatter import format_trade_alert
from bot.keyboards import trade_alert_keyboard
from bot.scanner import scan_top_ideas
from bot.storage import get_subscribers, save_signal

logger = logging.getLogger(__name__)

ALERT_TIMEFRAMES = ("1h", "2h", "3h", "4h", "6h")
_alert_timeframe_cursor = 0


def alert_min_score() -> float:
    return float(os.getenv("ALERT_MIN_SCORE", "75"))


def alert_timeframes() -> list[str]:
    configured = os.getenv("ALERT_TIMEFRAMES") or os.getenv("ALERT_TIMEFRAME")
    if not configured:
        return list(ALERT_TIMEFRAMES)
    return [part.strip().lower() for part in configured.split(",") if part.strip()]


def alert_scan_all_timeframes_per_run() -> bool:
    return os.getenv("ALERT_SCAN_ALL_TIMEFRAMES_PER_RUN", "").strip().lower() in {"1", "true", "yes", "on"}


def alert_timeframes_for_run() -> tuple[list[str], list[str]]:
    global _alert_timeframe_cursor
    configured = alert_timeframes()
    valid = [timeframe for timeframe in configured if timeframe in ALERT_TIMEFRAMES]
    skipped = [timeframe for timeframe in configured if timeframe not in ALERT_TIMEFRAMES]
    if alert_scan_all_timeframes_per_run() or len(valid) <= 1:
        return valid, skipped

    selected = valid[_alert_timeframe_cursor % len(valid)]
    _alert_timeframe_cursor += 1
    return [selected], skipped


def idea_score(idea: TradeIdea) -> float:
    return float(idea.setup_score or idea.confidence_score or 0)


def is_limit_order_alertable(idea: TradeIdea) -> bool:
    return idea.entry_status == "READY"


async def run_alert_scan(bot: Bot) -> dict[str, int | str]:
    exchange = os.getenv("ALERT_EXCHANGE", get_settings().default_exchange)
    min_score = alert_min_score()
    rejection_reasons: Counter[str] = Counter()
    subscribers = get_subscribers()
    if not subscribers:
        rejection_reasons["missing subscribers"] += 1
        logger.info(
            "telegram_alert_scan_complete subscribers=0 symbols_scanned=0 valid_ideas_found=0 eligible_alerts=0 alerts_sent=0 rejection_reasons=%s",
            dict(rejection_reasons),
        )
        return {"status": "ok", "subscribers": 0, "ideas": 0, "eligible": 0, "sent": 0, "alerts_sent": 0, "rejection_reasons": dict(rejection_reasons)}

    selected_exchange = exchange
    symbols_scanned = 0
    valid_ideas_found = 0
    all_ideas = []
    skipped_timeframes = 0
    scanned_timeframes = []
    timeframes_to_scan, skipped = alert_timeframes_for_run()
    for timeframe in skipped:
        skipped_timeframes += 1
        rejection_reasons["skipped_timeframe"] += 1
        logger.info("skipped_timeframe timeframe=%s allowed_timeframes=%s", timeframe, ",".join(ALERT_TIMEFRAMES))

    for timeframe in timeframes_to_scan:
        ideas, selected_exchange, scan_meta = await scan_top_ideas(timeframe, exchange)
        all_ideas.extend(ideas)
        scanned_timeframes.append(timeframe)
        symbols_scanned += int(scan_meta.get("symbols_scanned", 0) or 0)
        valid_ideas_found += int(scan_meta.get("valid_ideas_found", len(ideas)) or 0)
        rejection_reasons.update(scan_meta.get("rejection_reasons", {}))

    eligible_ideas = []
    limit_order_ideas = 0
    score_eligible_ideas = 0
    skipped_by_score = 0
    skipped_by_entry_status = 0
    for idea in all_ideas:
        score_ok = idea_score(idea) >= min_score
        limit_order_ok = is_limit_order_alertable(idea)
        if score_ok:
            score_eligible_ideas += 1
        if limit_order_ok:
            limit_order_ideas += 1
        if not score_ok:
            skipped_by_score += 1
            rejection_reasons["skipped_low_score"] += 1
            logger.info(
                "skipped_low_score symbol=%s timeframe=%s exchange=%s score=%s min_score=%s entry_status=%s",
                idea.symbol,
                idea.timeframe,
                idea.exchange,
                idea_score(idea),
                min_score,
                idea.entry_status,
            )
            continue
        if not limit_order_ok:
            skipped_by_entry_status += 1
            rejection_reasons["entry_status rejected/exhausted"] += 1
            continue
        eligible_ideas.append(idea)
    sent = 0
    skipped_by_dedup = 0
    for idea in eligible_ideas:
        if should_skip_alert(idea, namespace="telegram"):
            skipped_by_dedup += 1
            rejection_reasons["duplicate alert"] += 1
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
        for chat_id in subscribers:
            try:
                await bot.send_message(chat_id=chat_id, text=message, reply_markup=trade_alert_keyboard(signal_id))
                sent += 1
            except Exception as exc:
                rejection_reasons["send error"] += 1
                logger.warning("Could not send alert to chat %s: %s", chat_id, exc)
        mark_dedupe_sent(idea, namespace="telegram")

    logger.info(
        (
            "telegram_alert_scan_complete exchange=%s timeframes=%s symbols_scanned=%s valid_ideas_found=%s "
            "limit_order_ideas=%s score_eligible_ideas=%s eligible_alerts=%s alerts_sent=%s "
            "skipped_by_dedup=%s skipped_low_score=%s skipped_by_entry_status=%s skipped_timeframe=%s rejection_reasons=%s"
        ),
        selected_exchange,
        ",".join(scanned_timeframes),
        symbols_scanned,
        valid_ideas_found,
        limit_order_ideas,
        score_eligible_ideas,
        len(eligible_ideas),
        sent,
        skipped_by_dedup,
        skipped_by_score,
        skipped_by_entry_status,
        skipped_timeframes,
        dict(rejection_reasons),
    )
    return {
        "status": "ok",
        "exchange": selected_exchange,
        "timeframes": scanned_timeframes,
        "subscribers": len(subscribers),
        "ideas": len(all_ideas),
        "symbols_scanned": symbols_scanned,
        "valid_ideas_found": valid_ideas_found,
        "limit_order_ideas": limit_order_ideas,
        "score_eligible_ideas": score_eligible_ideas,
        "eligible": len(eligible_ideas),
        "min_score": min_score,
        "sent": sent,
        "alerts_sent": sent,
        "skipped_by_dedup": skipped_by_dedup,
        "skipped_by_score": skipped_by_score,
        "skipped_low_score": skipped_by_score,
        "skipped_timeframe": skipped_timeframes,
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
