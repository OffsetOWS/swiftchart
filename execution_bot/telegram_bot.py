from __future__ import annotations

import logging
from typing import Any

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from execution_bot.config import get_execution_settings
from execution_bot.models import BotStatus, SignalDecision
from execution_bot.storage import (
    account_balance,
    dashboard,
    daily_pnl,
    get_status,
    list_closed_trades,
    list_open_trades,
    log_event,
    runtime_base_risk_percent,
    set_state_value,
    set_status,
    trade_totals,
    weekly_pnl,
)

logger = logging.getLogger(__name__)

_application: Application | None = None


def telegram_enabled() -> bool:
    settings = get_execution_settings()
    return bool(
        settings.effective_telegram_bot_token
        and settings.effective_telegram_admin_id
        and settings.effective_telegram_polling_enabled
    )


def _admin_id() -> int | None:
    return get_execution_settings().effective_telegram_admin_id


def _is_admin(update: Update) -> bool:
    user = update.effective_user
    admin_id = _admin_id()
    return bool(user and admin_id and user.id == admin_id)


async def _guard(update: Update) -> bool:
    if _is_admin(update):
        return True
    user_id = update.effective_user.id if update.effective_user else None
    logger.warning("Ignoring unauthorized Telegram command from user_id=%s", user_id)
    log_event("telegram_unauthorized", {"user_id": user_id})
    return False


def _money(value: float | int | None) -> str:
    return f"${float(value or 0):,.2f}"


def _fmt_trade(row: dict[str, Any]) -> str:
    return (
        f"#{row.get('id')} {row.get('symbol')} {row.get('side')}\n"
        f"Entry: {float(row.get('entry') or 0):.6g} | SL: {float(row.get('stop_loss') or 0):.6g}\n"
        f"Size: {float(row.get('position_size') or 0):.6g} | Lev: {float(row.get('leverage') or 0):.2f}x\n"
        f"Risk: {_money(row.get('risk_amount'))} ({float(row.get('risk_percent') or 0):.2f}%) | Status: {row.get('status')}"
    )


def format_trade_alert(decision: SignalDecision) -> str:
    if not decision.accepted or decision.plan is None:
        return (
            "SwiftChart Signal Rejected\n\n"
            f"Pair: {decision.signal.pair}\n"
            f"Side: {decision.signal.side.value}\n"
            f"Entry: {decision.signal.entry:.6g}\n"
            f"Confidence: {decision.signal.confidence:.0f}%\n"
            f"Reason: {decision.reason}"
        )
    plan = decision.plan
    tp = plan.take_profits
    side = "LONG" if plan.side.value == "BUY" else "SHORT"
    return (
        "SwiftChart Trade Opened\n\n"
        f"Pair: {plan.signal.pair}\n"
        f"Side: {side}\n"
        f"Entry: {plan.entry:.6g}\n"
        f"SL: {plan.stop_loss:.6g}\n"
        f"TP1: {tp[0]['target']:.6g} ({tp[0]['close_percent']:.0f}%)\n"
        f"TP2: {tp[1]['target']:.6g} ({tp[1]['close_percent']:.0f}%)\n"
        f"TP3: {tp[2]['target']:.6g} ({tp[2]['close_percent']:.0f}%)\n"
        f"Leverage: {plan.leverage:.2f}x\n"
        f"Position Size: {plan.position_size:.6g}\n"
        f"Risk: {plan.risk_percent:.2f}% ({_money(plan.risk_amount)})\n"
        f"Confidence: {plan.signal.confidence:.0f}%\n"
        f"Market: {plan.market_condition}\n"
        f"Balance: {_money(account_balance())}\n"
        f"Mode: {plan.mode.value.upper()}\n"
        "Status: OPEN"
    )


async def notify_signal_decision(decision: SignalDecision) -> None:
    if _application is None or not _admin_id():
        return
    try:
        await _application.bot.send_message(chat_id=_admin_id(), text=format_trade_alert(decision))
        log_event("telegram_signal_alert", {"accepted": decision.accepted, "pair": decision.signal.pair})
    except Exception as exc:
        logger.exception("Could not send Telegram signal alert: %s", exc)
        log_event("telegram_error", {"error": str(exc), "action": "signal_alert"})


async def notify_execution_event(title: str, body: str, details: dict[str, Any] | None = None) -> None:
    if _application is None or not _admin_id():
        return
    text = f"{title}\n\n{body}"
    try:
        await _application.bot.send_message(chat_id=_admin_id(), text=text)
        log_event("telegram_execution_event", {"title": title, "details": details or {}})
    except Exception as exc:
        logger.exception("Could not send Telegram execution event: %s", exc)
        log_event("telegram_error", {"error": str(exc), "action": "execution_event"})


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update):
        return
    await update.effective_message.reply_text(
        "SwiftChart Execution Bot is online.\n\n"
        "Use /dashboard for the full control view or /help for commands."
    )
    log_event("telegram_command", {"command": "start"})


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update):
        return
    await update.effective_message.reply_text(
        "/status - bot status\n"
        "/balance - account balance\n"
        "/positions or /open_trades - open trades\n"
        "/closed_trades - recent closed trades\n"
        "/winrate - win rate\n"
        "/pnl - daily, weekly, total pnl\n"
        "/pause - pause new entries\n"
        "/resume - resume trading\n"
        "/kill - emergency kill switch\n"
        "/mode - active trading mode\n"
        "/risk - current risk settings\n"
        "/setrisk 2.5 - set runtime base risk percent\n"
        "/dashboard - full summary"
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update):
        return
    data = dashboard()
    await update.effective_message.reply_text(
        f"Status: {data['status'].upper()}\n"
        f"Mode: {data['mode'].upper()}\n"
        f"Open Trades: {len(data['active_trades'])}\n"
        f"Balance: {_money(data['balance'])}"
    )


async def cmd_balance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update):
        return
    await update.effective_message.reply_text(f"Balance: {_money(account_balance())}")


async def cmd_open_trades(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update):
        return
    rows = list_open_trades(10)
    text = "\n\n".join(_fmt_trade(row) for row in rows) if rows else "No open trades."
    await update.effective_message.reply_text(text)


async def cmd_closed_trades(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update):
        return
    rows = list_closed_trades(10)
    text = "\n\n".join(_fmt_trade(row) + f"\nPnL: {_money(row.get('final_pnl'))}" for row in rows) if rows else "No closed trades yet."
    await update.effective_message.reply_text(text)


async def cmd_winrate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update):
        return
    totals = trade_totals()
    await update.effective_message.reply_text(
        f"Win Rate: {float(totals['win_rate']):.1f}%\n"
        f"Closed Trades: {totals['total']}\n"
        f"Wins: {totals['wins']}"
    )


async def cmd_pnl(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update):
        return
    totals = trade_totals()
    await update.effective_message.reply_text(
        f"Daily PnL: {_money(daily_pnl())}\n"
        f"Weekly PnL: {_money(weekly_pnl())}\n"
        f"Total PnL: {_money(float(totals['pnl']))}"
    )


async def cmd_pause(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update):
        return
    set_status(BotStatus.paused)
    log_event("telegram_command", {"command": "pause"})
    await update.effective_message.reply_text("Trading paused. New signals will be rejected.")


async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update):
        return
    set_status(BotStatus.active)
    log_event("telegram_command", {"command": "resume"})
    await update.effective_message.reply_text("Trading resumed.")


async def cmd_kill(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update):
        return
    set_status(BotStatus.killed)
    log_event("telegram_command", {"command": "kill"})
    await update.effective_message.reply_text("Emergency kill switch is active. New signals will be rejected until /resume.")


async def cmd_mode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update):
        return
    settings = get_execution_settings()
    await update.effective_message.reply_text(
        f"Mode: {'LIVE' if settings.live_enabled else 'PAPER'}\n"
        f"Exchange: {settings.execution_exchange}\n"
        f"Live Confirm: {settings.execution_live_confirm}\n"
        f"Live Trading Flag: {settings.live_trading}\n"
        f"Auto Execute: {settings.auto_execute}"
    )


async def cmd_risk(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update):
        return
    settings = get_execution_settings()
    await update.effective_message.reply_text(
        f"Base Risk: {runtime_base_risk_percent():.2f}%\n"
        f"Max Risk: {settings.max_risk_percent:.2f}%\n"
        f"Daily Loss Limit: {settings.max_daily_loss_percent:.2f}%\n"
        f"Weekly Loss Limit: {settings.max_weekly_loss_percent:.2f}%\n"
        f"Max Open Trades: {settings.max_open_trades}\n"
        f"Max Leverage: {settings.max_leverage:.2f}x\n"
        f"Max Consecutive Losses: {settings.max_consecutive_losses}\n"
        f"Min Confidence: {settings.min_confidence_to_trade:.0f}%"
    )


async def cmd_setrisk(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update):
        return
    settings = get_execution_settings()
    if not context.args:
        await update.effective_message.reply_text("Usage: /setrisk 2.5")
        return
    try:
        value = float(context.args[0])
    except ValueError:
        await update.effective_message.reply_text("Risk must be a number, for example /setrisk 2.5")
        return
    if value <= 0 or value > settings.max_risk_percent:
        await update.effective_message.reply_text(f"Risk must be > 0 and <= {settings.max_risk_percent:.2f}%.")
        return
    set_state_value("base_risk_percent_override", str(value))
    log_event("telegram_command", {"command": "setrisk", "value": value})
    await update.effective_message.reply_text(f"Runtime base risk set to {value:.2f}%.")


async def cmd_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update):
        return
    data = dashboard()
    await update.effective_message.reply_text(
        "SwiftChart Execution Dashboard\n\n"
        f"Balance: {_money(data['balance'])}\n"
        f"Daily PnL: {_money(data['daily_pnl'])}\n"
        f"Weekly PnL: {_money(data['weekly_pnl'])}\n"
        f"Total PnL: {_money(data['profit_loss'])}\n"
        f"Win Rate: {float(data['win_rate']):.1f}%\n"
        f"Total Trades: {data['total_trades']}\n"
        f"Open Trades: {len(data['active_trades'])}\n"
        f"Open Risk: {_money(data['open_risk'])}\n"
        f"Drawdown: {_money(data['daily_drawdown'])}\n"
        f"Mode: {data['mode'].upper()}\n"
        f"Status: {data['status'].upper()}\n"
        f"Base Risk: {float(data['base_risk_percent']):.2f}%"
    )


def _register_handlers(application: Application) -> None:
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("help", cmd_help))
    application.add_handler(CommandHandler("status", cmd_status))
    application.add_handler(CommandHandler("balance", cmd_balance))
    application.add_handler(CommandHandler("positions", cmd_open_trades))
    application.add_handler(CommandHandler("open_trades", cmd_open_trades))
    application.add_handler(CommandHandler("closed_trades", cmd_closed_trades))
    application.add_handler(CommandHandler("winrate", cmd_winrate))
    application.add_handler(CommandHandler("pnl", cmd_pnl))
    application.add_handler(CommandHandler("pause", cmd_pause))
    application.add_handler(CommandHandler("resume", cmd_resume))
    application.add_handler(CommandHandler("kill", cmd_kill))
    application.add_handler(CommandHandler("mode", cmd_mode))
    application.add_handler(CommandHandler("risk", cmd_risk))
    application.add_handler(CommandHandler("setrisk", cmd_setrisk))
    application.add_handler(CommandHandler("dashboard", cmd_dashboard))


async def start_telegram_bot() -> None:
    global _application
    if not telegram_enabled():
        logger.info("Execution Telegram bot disabled. Set TELEGRAM_BOT_TOKEN and TELEGRAM_ADMIN_ID to enable it.")
        return
    if _application is not None:
        return
    settings = get_execution_settings()
    logger.info("Starting execution Telegram bot in admin-only mode for admin_id=%s", settings.effective_telegram_admin_id)
    application = Application.builder().token(settings.effective_telegram_bot_token).build()
    _register_handlers(application)
    await application.initialize()
    await application.start()
    if application.updater is None:
        raise RuntimeError("Telegram updater is not available.")
    await application.updater.start_polling(drop_pending_updates=True)
    _application = application
    log_event("telegram_startup", {"admin_id": settings.effective_telegram_admin_id})
    try:
        warning = "\nLIVE TRADING ACTIVE" if settings.live_enabled else ""
        await application.bot.send_message(chat_id=settings.effective_telegram_admin_id, text=f"SwiftChart Execution Bot started.{warning}")
    except Exception as exc:
        logger.warning("Telegram bot started, but startup DM failed: %s", exc)
        log_event("telegram_startup_dm_failed", {"error": str(exc)})


async def stop_telegram_bot() -> None:
    global _application
    if _application is None:
        return
    logger.info("Stopping execution Telegram bot.")
    if _application.updater is not None:
        await _application.updater.stop()
    await _application.stop()
    await _application.shutdown()
    _application = None
