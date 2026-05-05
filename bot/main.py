import asyncio
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from telegram.ext import Application, CallbackQueryHandler, CommandHandler

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(ROOT))

from bot.handlers import (  # noqa: E402
    alerts_status,
    analyze,
    button_handler,
    check_trades,
    help_command,
    history,
    start,
    stats_command,
    strategy,
    subscribe,
    top,
    unsubscribe,
)
from app.services.scanner import start_background_scanner  # noqa: E402
from app.utils.database import init_db  # noqa: E402


async def post_init(_application: Application) -> None:
    start_background_scanner()


def build_application() -> Application:
    load_dotenv(ROOT / ".env")
    load_dotenv(ROOT / "bot" / ".env")
    init_db()
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is required.")

    application = Application.builder().token(token).post_init(post_init).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("analyze", analyze))
    application.add_handler(CommandHandler("top", top))
    application.add_handler(CommandHandler("subscribe", subscribe))
    application.add_handler(CommandHandler("unsubscribe", unsubscribe))
    application.add_handler(CommandHandler("alerts", alerts_status))
    application.add_handler(CommandHandler("history", history))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("checktrades", check_trades))
    application.add_handler(CommandHandler("strategy", strategy))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CallbackQueryHandler(button_handler))
    return application


def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    asyncio.set_event_loop(asyncio.new_event_loop())
    application = build_application()
    application.run_polling(allowed_updates=["message", "callback_query"], drop_pending_updates=True)


if __name__ == "__main__":
    main()
