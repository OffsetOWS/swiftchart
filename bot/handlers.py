import logging

from telegram import Update
from telegram.ext import ContextTypes

from app.config import SUPPORTED_TIMEFRAMES, get_settings
from app.models.schemas import RiskSettings
from app.services.market_data import get_candles_cached
from app.services.trade_history import check_trade_outcomes, list_trade_history, save_trade_ideas, stats
from app.strategy.trade_ideas import analyze_dataframe
from bot.formatter import format_analysis, format_history, format_paper_trades, format_stats, format_top_ideas, help_text, strategy_text
from bot.keyboards import command_keyboard, main_menu_keyboard
from bot.paper_trading import create_paper_trade, list_open_paper_trades, list_paper_trades, supabase_enabled
from bot.scanner import scan_top_ideas
from bot.storage import add_subscriber, get_latest_signal, get_signal, get_subscribers, remove_subscriber

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
    df = await get_candles_cached(selected_exchange, symbol.upper(), timeframe, 320)
    if len(df) < 80:
        raise ValueError("Not enough candle history for analysis.")
    htf_dfs = []
    for htf in higher_timeframes_for(timeframe):
        try:
            htf_dfs.append(await get_candles_cached(selected_exchange, symbol.upper(), htf, 240))
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
        ideas, exchange, _ = await scan_top_ideas(timeframe)
        await status.edit_text(format_top_ideas(ideas[:5], timeframe, exchange))
    except Exception as exc:
        logger.exception("Top scan failed")
        await status.edit_text(f"Could not scan top ideas: {exc}\n\nNot financial advice. Manage your risk.")


async def subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    add_subscriber(chat_id)
    await update.effective_message.reply_text(
        "Alerts enabled.\n\n"
        "SwiftChart will notify this chat when a new valid setup appears. "
        "Only READY 1H, 2H, 3H, 4H, and 6H setups scoring 75/100 or higher are eligible.\n\n"
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


async def my_trades(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not supabase_enabled():
        await update.effective_message.reply_text("Paper trading is temporarily unavailable.")
        return
    try:
        records = await list_paper_trades(update.effective_user.id)
        await update.effective_message.reply_text(format_paper_trades(records))
    except Exception:
        logger.exception("Could not list Telegram paper trades")
        await update.effective_message.reply_text("Could not load your paper trades right now.")


async def open_trades(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not supabase_enabled():
        await update.effective_message.reply_text("Paper trading is temporarily unavailable.")
        return
    try:
        records = await list_open_paper_trades(update.effective_user.id)
        await update.effective_message.reply_text(format_paper_trades(records, open_only=True))
    except Exception:
        logger.exception("Could not list open Telegram paper trades")
        await update.effective_message.reply_text("Could not load your open paper trades right now.")


async def signal_analysis(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = getattr(context, "args", None) or []
    selector = args[0] if args else None
    signal = get_latest_signal(selector)
    if not signal:
        if selector:
            await update.effective_message.reply_text(
                f"No stored signal analysis found for {selector}.\n"
                "Use /analysis for the latest signal, or /analysis BTCUSDT for a pair."
            )
        else:
            await update.effective_message.reply_text("No stored signal analysis yet. Wait for the next trade alert.")
        return
    await update.effective_message.reply_text(
        f"{signal['analysis']}\n\nSignal ID: {signal['signal_id']}\n\nNot financial advice. Manage your risk."
    )


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data or ""
    if data.startswith("paper:"):
        signal = get_signal(data.removeprefix("paper:"))
        if not signal:
            await query.message.reply_text("This signal has expired. Wait for the next alert.")
            return
        if not supabase_enabled():
            await query.message.reply_text("Paper trading is temporarily unavailable.")
            return
        try:
            trade, already_exists = await create_paper_trade(query.from_user.id, signal)
            prefix = "Already paper trading" if already_exists else "Paper trade opened"
            await query.message.reply_text(
                f"🧪 {prefix}: {trade['pair']} {str(trade['side']).upper()}\n"
                f"Entry {trade['entry']} | SL {trade['stop_loss']} | TP1 {trade['tp1']} | TP2 {trade['tp2']}\n\n"
                "Simulated only. No real order was placed."
            )
        except Exception:
            logger.exception("Could not create Telegram paper trade")
            await query.message.reply_text("Could not open that paper trade right now.")
    elif data == "analyze_help":
        await query.message.reply_text("Type /analyze SOLUSDT 4h to analyze a coin.")
    elif data == "top":
        await top(update, context)
    elif data == "subscribe":
        await subscribe(update, context)
    elif data == "strategy":
        await query.message.reply_text(strategy_text())
    elif data == "help":
        await query.message.reply_text(help_text(), reply_markup=command_keyboard())
