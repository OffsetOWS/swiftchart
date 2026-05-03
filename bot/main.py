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

from bot.handlers import analyze, button_handler, help_command, start, strategy, top  # noqa: E402


def build_application() -> Application:
    load_dotenv(ROOT / ".env")
    load_dotenv(ROOT / "bot" / ".env")
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is required.")

    application = Application.builder().token(token).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("analyze", analyze))
    application.add_handler(CommandHandler("top", top))
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
    application.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
