import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Request
from telegram import Update
from telegram.ext import Application

from bot.main import build_application

logger = logging.getLogger(__name__)
telegram_app: Application | None = None


def webhook_url() -> str | None:
    explicit_url = os.getenv("TELEGRAM_WEBHOOK_URL") or os.getenv("WEBHOOK_URL")
    if explicit_url:
        return explicit_url.rstrip("/")

    render_url = os.getenv("RENDER_EXTERNAL_URL")
    if render_url:
        return f"{render_url.rstrip('/')}/telegram/webhook"

    return None


@asynccontextmanager
async def lifespan(_: FastAPI):
    global telegram_app
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)

    telegram_app = build_application()
    await telegram_app.initialize()
    await telegram_app.start()

    url = webhook_url()
    if url:
        await telegram_app.bot.set_webhook(
            url=url,
            allowed_updates=["message", "callback_query"],
            secret_token=os.getenv("TELEGRAM_WEBHOOK_SECRET") or None,
            drop_pending_updates=True,
        )
        logger.info("Telegram webhook configured.")
    else:
        logger.warning("No webhook URL configured. Set TELEGRAM_WEBHOOK_URL or WEBHOOK_URL.")

    try:
        yield
    finally:
        if telegram_app is not None:
            await telegram_app.bot.delete_webhook(drop_pending_updates=False)
            await telegram_app.stop()
            await telegram_app.shutdown()


app = FastAPI(title="SwiftChart Bot", lifespan=lifespan)


@app.get("/")
async def root():
    return {"name": "SwiftChart Bot", "status": "ok"}


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/telegram/webhook")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
):
    secret = os.getenv("TELEGRAM_WEBHOOK_SECRET")
    if secret and x_telegram_bot_api_secret_token != secret:
        raise HTTPException(status_code=403, detail="Invalid webhook secret.")

    if telegram_app is None:
        raise HTTPException(status_code=503, detail="Telegram app is not ready.")

    payload = await request.json()
    update = Update.de_json(payload, telegram_app.bot)
    await telegram_app.process_update(update)
    return {"ok": True}
