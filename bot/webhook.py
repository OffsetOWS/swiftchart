import asyncio
import logging
import os
import sys
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(ROOT))

from fastapi import FastAPI, Header, HTTPException, Request
from telegram import Update
from telegram.ext import Application

from app.services.scanner import start_background_scanner
from app.utils.secure_logging import install_secure_logging
from bot.alerts import alert_loop, run_alert_scan
from bot.main import build_application

logger = logging.getLogger(__name__)
telegram_app: Application | None = None
alert_task: asyncio.Task | None = None
_alert_run_requests: dict[str, deque[float]] = defaultdict(deque)


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
    global alert_task, telegram_app
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )
    install_secure_logging()
    logging.getLogger("httpx").setLevel(logging.WARNING)

    telegram_app = build_application()
    await telegram_app.initialize()
    await telegram_app.start()
    start_background_scanner()

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

    if os.getenv("ALERTS_ENABLED", "true").lower() == "true":
        alert_task = asyncio.create_task(alert_loop(telegram_app.bot))
        logger.info("Telegram trade alert scanner enabled.")

    try:
        yield
    finally:
        if alert_task is not None:
            alert_task.cancel()
            try:
                await alert_task
            except asyncio.CancelledError:
                pass
        if telegram_app is not None:
            await telegram_app.stop()
            await telegram_app.shutdown()


app = FastAPI(title="SwiftChart Bot", lifespan=lifespan)


@app.get("/")
async def root():
    return {"name": "SwiftChart Bot", "status": "ok"}


@app.get("/health")
async def health():
    return {"status": "ok"}


def check_alert_secret(secret: str | None, x_swiftchart_alert_secret: str | None) -> None:
    expected = os.getenv("ALERTS_RUN_SECRET")
    if expected and secret != expected and x_swiftchart_alert_secret != expected:
        raise HTTPException(status_code=403, detail="Invalid alert secret.")


def check_alert_rate_limit(request: Request) -> None:
    limit = int(os.getenv("ALERTS_RUN_RATE_LIMIT_PER_MINUTE", "6"))
    forwarded = request.headers.get("x-forwarded-for", "")
    client = forwarded.split(",", 1)[0].strip() if forwarded else (request.client.host if request.client else "unknown")
    now = time.monotonic()
    bucket = _alert_run_requests[client]
    while bucket and now - bucket[0] > 60:
        bucket.popleft()
    if len(bucket) >= limit:
        raise HTTPException(status_code=429, detail="Rate limit exceeded.")
    bucket.append(now)


@app.get("/alerts/run")
async def run_alerts(request: Request, secret: str | None = None, x_swiftchart_alert_secret: str | None = Header(default=None)):
    check_alert_secret(secret, x_swiftchart_alert_secret)
    check_alert_rate_limit(request)
    if telegram_app is None:
        raise HTTPException(status_code=503, detail="Telegram app is not ready.")
    return await run_alert_scan(telegram_app.bot)


@app.post("/alerts/run")
async def run_alerts_post(request: Request, secret: str | None = None, x_swiftchart_alert_secret: str | None = Header(default=None)):
    return await run_alerts(request=request, secret=secret, x_swiftchart_alert_secret=x_swiftchart_alert_secret)


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
