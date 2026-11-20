from __future__ import annotations

import html
import logging
import re

from telegram import Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import ContextTypes

from trading_agent.config import TradingAgentSettings
from trading_agent.service import TradingAgentService


logger = logging.getLogger("swiftchart.trading_agent.telegram")
SERVICE_KEY = "trading_agent_service"
SETTINGS_KEY = "trading_agent_settings"
MAX_TELEGRAM_MESSAGE = 4000
MARKDOWN_BOLD_PATTERN = re.compile(r"\*\*(.+?)\*\*")
MARKDOWN_HEADING_PATTERN = re.compile(r"(?m)^\s*#{1,6}\s+(.+?)\s*$")


def _dependencies(context: ContextTypes.DEFAULT_TYPE) -> tuple[TradingAgentSettings, TradingAgentService]:
    return (
        context.application.bot_data[SETTINGS_KEY],
        context.application.bot_data[SERVICE_KEY],
    )


def _ids(update: Update) -> tuple[int | None, int | None]:
    user_id = update.effective_user.id if update.effective_user else None
    chat_id = update.effective_chat.id if update.effective_chat else None
    return user_id, chat_id


async def _authorize(update: Update, context: ContextTypes.DEFAULT_TYPE) -> tuple[int, int] | None:
    settings, _ = _dependencies(context)
    user_id, chat_id = _ids(update)
    if settings.is_authorized(user_id=user_id, chat_id=chat_id) and user_id is not None and chat_id is not None:
        return user_id, chat_id
    logger.warning("telegram_access_denied chat_id=%s user_id=%s", chat_id, user_id)
    if update.effective_message:
        await update.effective_message.reply_text("This trading-agent bot is private.")
    return None


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _authorize(update, context) is None:
        return
    await update.effective_message.reply_text(
        "SwiftChart Trading Agent is online in read-only mode. Ask a normal question about scans, "
        "signals, market snapshots, currency strength, volume profile, active trades, or a single signal status. Aggregate performance is unavailable. "
        "It cannot place or modify trades."
    )


async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    identity = await _authorize(update, context)
    if identity is None:
        return
    user_id, chat_id = identity
    _, service = _dependencies(context)
    service.reset(chat_id=chat_id, user_id=user_id)
    await update.effective_message.reply_text("This trading-agent conversation has been reset.")


def _chunks(text: str) -> list[str]:
    if len(text) <= MAX_TELEGRAM_MESSAGE:
        return [text]
    chunks: list[str] = []
    remaining = text
    while remaining:
        split_at = remaining.rfind("\n", 0, MAX_TELEGRAM_MESSAGE)
        if split_at < MAX_TELEGRAM_MESSAGE // 2:
            split_at = MAX_TELEGRAM_MESSAGE
        chunks.append(remaining[:split_at].strip())
        remaining = remaining[split_at:].strip()
    return [chunk for chunk in chunks if chunk]


def telegram_html(text: str) -> str:
    """Render a small, safe Markdown subset as Telegram HTML."""
    escaped = html.escape(text or "", quote=False)
    escaped = MARKDOWN_HEADING_PATTERN.sub(r"<b>\1</b>", escaped)
    escaped = MARKDOWN_BOLD_PATTERN.sub(r"<b>\1</b>", escaped)
    return escaped.replace("**", "")


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    identity = await _authorize(update, context)
    if identity is None or update.effective_message is None:
        return
    text = (update.effective_message.text or "").strip()
    if not text:
        return
    user_id, chat_id = identity
    _, service = _dependencies(context)
    try:
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        response = await service.handle_message(chat_id=chat_id, user_id=user_id, text=text)
        for chunk in _chunks(response):
            await update.effective_message.reply_text(
                telegram_html(chunk),
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
    except Exception as exc:
        logger.warning(
            "telegram_agent_turn_failed chat_id=%s user_id=%s error_type=%s",
            chat_id,
            user_id,
            type(exc).__name__,
        )
        await update.effective_message.reply_text(
            "I could not complete that read-only analysis right now. No trade was placed or changed."
        )
