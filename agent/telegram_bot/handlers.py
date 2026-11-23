from __future__ import annotations

import html
import logging
import re
import asyncio
from io import BytesIO

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatAction, ParseMode
from telegram.ext import ContextTypes

from trading_agent.config import TradingAgentSettings
from trading_agent.service import TradingAgentService
from trading_agent.charts import CandleCharts


logger = logging.getLogger("swiftchart.trading_agent.telegram")
SERVICE_KEY = "trading_agent_service"
SETTINGS_KEY = "trading_agent_settings"
MAX_TELEGRAM_MESSAGE = 4000
MARKDOWN_BOLD_PATTERN = re.compile(r"\*\*(.+?)\*\*")
MARKDOWN_HEADING_PATTERN = re.compile(r"(?m)^\s*#{1,6}\s+(.+?)\s*$")
CHARTS = CandleCharts()


def market_keyboard(market):
    if not market:
        return None
    def button(label, action):
        return InlineKeyboardButton(label, callback_data=f'as:{market["token"]}:{action}')
    return InlineKeyboardMarkup([
        [button('Chart', 'chart'), button('Less detail' if market.get('detail') else 'More detail', 'detail')],
        [button('1H', 'tf1h'), button('4H', 'tf4h'), button('1D', 'tf1d')],
        [button('News', 'news'), button('Alert me', 'alert')],
    ])


async def send_reply(message, reply):
    for chunk in _chunks(reply.text):
        await message.reply_text(telegram_html(chunk), parse_mode=ParseMode.HTML,
                                 disable_web_page_preview=True, reply_markup=market_keyboard(reply.context))


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
    identity = await _authorize(update, context)
    if identity is None:
        return
    if context.args:
        match = re.fullmatch(r's_([cf])_([A-Za-z0-9_-]{1,58})', context.args[0])
        if match:
            _, service = _dependencies(context)
            try:
                reply = await service.open_signal(identity[1], identity[0], 'crypto' if match[1]=='c' else 'forex', match[2])
                await send_reply(update.effective_message, reply)
            except Exception as exc:
                logger.warning('signal_context_unavailable type=%s', type(exc).__name__)
                await update.effective_message.reply_text('That signal is unavailable. Ask for its market to see a fresh snapshot.')
            return
    await update.effective_message.reply_text(
        "SwiftChart Trading Agent is online in read-only mode. Ask a normal question about scans, "
        "signals, market snapshots, currency strength, volume profile, active trades, or a single signal status. Aggregate performance is unavailable. "
        "It cannot place or modify trades."
    )


async def alerts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    identity = await _authorize(update, context)
    if identity is None:
        return
    _, service = _dependencies(context)
    rows = await asyncio.to_thread(service.alerts.list, identity[0])
    if not rows:
        await update.effective_message.reply_text('You have no active price alerts.')
        return
    lines = ['Your active price alerts (your targets):']
    buttons = []
    for row in rows:
        lines.append(f'{row["symbol"]}: {row["target"]} · expires {row["expires_at"][:10]}')
        buttons.append([InlineKeyboardButton(f'Cancel {row["symbol"]} at {row["target"]}', callback_data='aa:'+row['id'])])
    await update.effective_message.reply_text(telegram_html('\n'.join(lines)), reply_markup=InlineKeyboardMarkup(buttons))


async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    identity = await _authorize(update, context)
    if identity is None or query is None:
        return
    await query.answer()
    user_id, chat_id = identity
    _, service = _dependencies(context)
    cancel = re.fullmatch(r'aa:([A-Za-z0-9_-]{8})', query.data or '')
    if cancel:
        removed = await asyncio.to_thread(service.alerts.cancel, user_id, cancel[1])
        await query.message.reply_text('Alert cancelled.' if removed else 'That alert is no longer active.')
        await alerts(update, context)
        return
    match = re.fullmatch(r'as:([A-Za-z0-9_-]{12}):(chart|detail|tf1h|tf4h|tf1d|news|alert)', query.data or '')
    if not match:
        return
    try:
        reply = await service.interact(chat_id=chat_id, user_id=user_id, token=match[1], action=match[2])
        if match[2] == 'chart' and reply.data is not None:
            raw = await asyncio.to_thread(CHARTS.render, reply.data)
            photo = BytesIO(raw); photo.name='swiftchart.png'
            await query.message.reply_photo(photo, caption=telegram_html(f'{reply.data["symbol"]} {reply.data["timeframe"]} · Data: {reply.data["as_of"]}\nNot financial advice.'), reply_markup=market_keyboard(reply.context))
        elif match[2] in {'news','alert'} or not query.message.text:
            await send_reply(query.message, reply)
        elif query.message.text != reply.text:
            await query.edit_message_text(telegram_html(reply.text), parse_mode=ParseMode.HTML,
                                          disable_web_page_preview=True, reply_markup=market_keyboard(reply.context))
        else:
            await query.edit_message_reply_markup(reply_markup=market_keyboard(reply.context))
    except Exception as exc:
        logger.warning('market_callback_failed action=%s type=%s', match[2], type(exc).__name__)
        text = str(exc) if isinstance(exc, ValueError) else 'That API view is temporarily unavailable. Try again shortly.'
        await query.message.reply_text(telegram_html(text))


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
        response = await service.handle_turn(chat_id=chat_id, user_id=user_id, text=text)
        await send_reply(update.effective_message, response)
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
