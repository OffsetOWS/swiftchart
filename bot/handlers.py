import logging

from telegram import Update
from telegram.ext import ContextTypes

from app.config import DEFAULT_SCAN_LIST, SUPPORTED_TIMEFRAMES, get_settings
from app.exchanges.factory import get_exchange
from app.models.schemas import RiskSettings
from app.services.trade_history import check_trade_outcomes, list_trade_history, save_trade_ideas, stats
from app.strategy.trade_ideas import analyze_dataframe
from bot.formatter import format_analysis, format_history, format_stats, format_top_ideas, help_text, strategy_text
from bot.keyboards import command_keyboard, main_menu_keyboard
from bot.storage import add_subscriber, get_subscribers, remove_subscriber

logger = logging.getLogger(__name__)


def normalize_timeframe(value: str) -> str:
    normalized = value.strip().lower()
    if normalized == "1d":
        return "1d"
    return normalized


def validate_timeframe(value: str) -> str | None:
    timeframe = normalize_timeframe(value)
    if timeframe not in SUPPORTED_TIMEFRAMES:
        return None
    return timeframe


def higher_timeframes_for(timeframe: str) -> list[str]:
    if timeframe in {"30m", "1h"}:
        return ["4h", "1d"]
    if timeframe in {"2h", "4h", "6h", "8h", "12h"}:
        return ["1d"]
    return []


async def run_analysis(symbol: str, timeframe: str, exchange: str | None = None):
    settings = get_settings()
    selected_exchange = exchange or settings.default_exchange
    client = get_exchange(selected_exchange)
    df = await client.get_candles(symbol.upper(), timeframe, 320)
    if len(df) < 80:
        raise ValueError("Not enough candle history for analysis.")
    htf_dfs = []
    for htf in higher_timeframes_for(timeframe):
        try:
            htf_dfs.append(await client.get_candles(symbol.upper(), htf, 240))
        except Exception as exc:
            logger.warning("HTF fetch failed for %s %s: %s", symbol, htf, exc)
    risk = RiskSettings(
        account_size=settings.default_account_size,
        risk_per_trade_pct=settings.default_risk_per_trade,
        min_rr=settings.default_min_rr,
        max_open_trades=settings.default_max_open_trades,
        preferred_timeframe=timeframe,
    )
    analysis = analyze_dataframe(symbol.upper(), timeframe, selected_exchange, df, risk, htf_dfs)
    save_trade_ideas(analysis.trade_ideas)
    return analysis


async def scan_top_ideas(timeframe: str, exchange: str | None = None):
    settings = get_settings()
    selected_exchange = exchange or settings.default_exchange
    client = get_exchange(selected_exchange)
    risk = RiskSettings(
        account_size=settings.default_account_size,
        risk_per_trade_pct=settings.default_risk_per_trade,
        min_rr=settings.default_min_rr,
        max_open_trades=settings.default_max_open_trades,
        preferred_timeframe=timeframe,
    )
    ideas = []
    for symbol in DEFAULT_SCAN_LIST:
        try:
            df = await client.get_candles(symbol, timeframe, 260)
            if len(df) >= 80:
                htf_dfs = []
                for htf in higher_timeframes_for(timeframe):
                    try:
                        htf_dfs.append(await client.get_candles(symbol, htf, 220))
                    except Exception:
                        continue
                analysis = analyze_dataframe(symbol, timeframe, selected_exchange, df, risk, htf_dfs)
                ideas.extend(analysis.trade_ideas)
        except Exception as exc:
            logger.warning("Top idea scan failed for %s on %s: %s", symbol, selected_exchange, exc)
    ranked = sorted(ideas, key=lambda idea: idea.rank_score, reverse=True)[:5]
    save_trade_ideas(ranked)
    return ranked, selected_exchange


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = (
        "Welcome to SwiftChart Bot.\n\n"
        "SwiftChart helps traders detect support, resistance, liquidity sweeps, range conditions, "
        "and high-probability crypto trade ideas.\n\n"
        "Choose an option below or type /help."
    )
    await update.effective_message.reply_text(
        message,
        reply_markup=main_menu_keyboard(),
    )
    await update.effective_message.reply_text("Quick commands:", reply_markup=command_keyboard())


async def analyze(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) < 2:
        await update.effective_message.reply_text("Usage: /analyze SOLUSDT 4h")
        return

    symbol = context.args[0].upper()
    timeframe = validate_timeframe(context.args[1])
    if timeframe is None:
        await update.effective_message.reply_text("Unsupported timeframe. Use: 30m, 1h, 2h, 4h, 6h, 8h, 12h, 1D")
        return

    status = await update.effective_message.reply_text(f"Analyzing {symbol} on {timeframe.upper()}...")
    try:
        analysis = await run_analysis(symbol, timeframe)
        await status.edit_text(format_analysis(analysis))
    except Exception as exc:
        logger.exception("Analysis failed")
        await status.edit_text(f"Could not analyze {symbol}: {exc}\n\nNot financial advice. Manage your risk.")


async def top(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = getattr(context, "args", None) or []
    timeframe = validate_timeframe(args[0]) if args else get_settings().default_timeframe
    if timeframe is None:
        await update.effective_message.reply_text("Unsupported timeframe. Use: 30m, 1h, 2h, 4h, 6h, 8h, 12h, 1D")
        return

    status = await update.effective_message.reply_text(f"Scanning top ideas on {timeframe.upper()}...")
    try:
        ideas, exchange = await scan_top_ideas(timeframe)
        await status.edit_text(format_top_ideas(ideas, timeframe, exchange))
    except Exception as exc:
        logger.exception("Top scan failed")
        await status.edit_text(f"Could not scan top ideas: {exc}\n\nNot financial advice. Manage your risk.")


async def subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    add_subscriber(chat_id)
    await update.effective_message.reply_text(
        "Alerts enabled.\n\n"
        "SwiftChart will notify this chat when a new valid setup appears. "
        "Only setups scoring 65/100 or higher are eligible.\n\n"
        "Use /unsubscribe to stop alerts.\n\n"
        "Not financial advice. Manage your risk."
    )


async def unsubscribe(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    remove_subscriber(chat_id)
    await update.effective_message.reply_text("Alerts disabled for this chat.")


async def alerts_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    subscribers = get_subscribers()
    enabled = update.effective_chat.id in subscribers
    await update.effective_message.reply_text(
        f"Alert status: {'enabled' if enabled else 'disabled'}\n"
        f"Subscribers: {len(subscribers)}\n\n"
        "Use /subscribe to receive setup alerts or /unsubscribe to stop them."
    )


async def history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    records = list_trade_history({})[:5]
    await update.effective_message.reply_text(format_history(records))


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(format_stats(stats()))


async def check_trades(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    status = await update.effective_message.reply_text("Checking saved trade outcomes...")
    try:
        result = await check_trade_outcomes()
        await status.edit_text(f"Trade outcome check complete.\nChecked: {result['checked']}\nUpdated: {result['updated']}")
    except Exception as exc:
        logger.exception("Trade outcome check failed")
        await status.edit_text(f"Could not check trades: {exc}")


async def strategy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(strategy_text())


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(help_text(), reply_markup=command_keyboard())


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if query.data == "analyze_help":
        await query.message.reply_text("Type /analyze SOLUSDT 4h to analyze a coin.")
    elif query.data == "top":
        await top(update, context)
    elif query.data == "subscribe":
        await subscribe(update, context)
    elif query.data == "strategy":
        await query.message.reply_text(strategy_text())
    elif query.data == "help":
        await query.message.reply_text(help_text(), reply_markup=command_keyboard())
