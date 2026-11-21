from __future__ import annotations

import logging
import asyncio
from pathlib import Path
import sys

from telegram import BotCommand
from telegram.constants import ParseMode
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, Defaults, MessageHandler, filters


ROOT = Path(__file__).resolve().parents[2]

from trading_agent.config import TradingAgentSettings  # noqa: E402
from trading_agent.service import TradingAgentService  # noqa: E402
from telegram_bot.handlers import (  # noqa: E402
    SERVICE_KEY,
    SETTINGS_KEY,
    handle_text,
    reset,
    start,
    alerts,
    callback,
)
from telegram_bot.logging_config import configure_logging  # noqa: E402


async def post_init(application: Application) -> None:
    # Load the pinned runtime before polling; cold imports are not a model call.
    service = application.bot_data[SERVICE_KEY]
    await asyncio.to_thread(service.orchestrator.register_tools)
    await service.warm_up()
    await application.bot.set_my_commands(
        [
            BotCommand("start", "Describe the read-only trading agent"),
            BotCommand("reset", "Reset this agent conversation"),
            BotCommand("alerts", "List and cancel your price alerts"),
        ]
    )
    application.bot_data['alert_worker'] = asyncio.create_task(alert_worker(application))


async def alert_worker(application):
    from trading_agent.alerts import deliver_alerts
    while True:
        try:
            await deliver_alerts(application.bot_data[SERVICE_KEY], application.bot)
        except Exception as exc:
            logging.getLogger('swiftchart.trading_agent').warning('alert_poll_failed type=%s',type(exc).__name__)
        await asyncio.sleep(5)


async def post_shutdown(application):
    worker = application.bot_data.pop('alert_worker', None)
    if worker:
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)


def build_application(
    *,
    settings: TradingAgentSettings | None = None,
    service: TradingAgentService | None = None,
) -> Application:
    selected_settings = settings or TradingAgentSettings.from_env()
    selected_settings.validate_for_bot()
    selected_service = service or TradingAgentService(selected_settings)
    application = (
        Application.builder()
        .token(selected_settings.telegram_bot_token)
        .concurrent_updates(8)
        .defaults(Defaults(parse_mode=ParseMode.HTML))
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    application.bot_data[SETTINGS_KEY] = selected_settings
    application.bot_data[SERVICE_KEY] = selected_service
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("reset", reset))
    application.add_handler(CommandHandler("alerts", alerts))
    application.add_handler(CallbackQueryHandler(callback, pattern=r'^(as:|aa:)'))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    return application


def main() -> None:
    # Runtime credentials and the unchanged allowlists are supplied by the service manager.
    # Never load the web/backend .env or start the signal bot from this process.
    settings = TradingAgentSettings.from_env()
    settings.validate_for_bot()
    configure_logging(settings)
    logging.getLogger("swiftchart.trading_agent").info(
        "trading_agent_bot_starting bedrock_region=%s bedrock_model=%s",
        settings.aws_region,
        settings.bedrock_model_id,
    )
    application = build_application(settings=settings)
    application.run_polling(allowed_updates=["message", "callback_query"], drop_pending_updates=False)


if __name__ == "__main__":
    main()
