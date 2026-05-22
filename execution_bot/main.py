from __future__ import annotations

import asyncio
import logging

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from execution_bot.config import get_execution_settings
from execution_bot.models import BotStatus, SignalDecision, SignalIn
from execution_bot.security import install_secure_logging, require_signed_request
from execution_bot.service import process_signal, sync_live_account_state
from execution_bot.storage import dashboard, init_db, set_status
from execution_bot.telegram_bot import start_telegram_bot, stop_telegram_bot

app = FastAPI(title="SwiftChart Execution Bot")
logger = logging.getLogger(__name__)
_sync_task: asyncio.Task | None = None

execution_origins = [origin.strip() for origin in get_execution_settings().execution_cors_origins.split(",") if origin.strip()]
if execution_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=execution_origins,
        allow_methods=["POST"],
        allow_headers=["Content-Type", "X-SwiftChart-Timestamp", "X-SwiftChart-Nonce", "X-SwiftChart-Signature"],
    )


@app.on_event("startup")
async def startup() -> None:
    global _sync_task
    install_secure_logging()
    init_db()
    settings = get_execution_settings()
    if settings.live_enabled and len(settings.execution_webhook_secret) < 32:
        raise RuntimeError("EXECUTION_WEBHOOK_SECRET must be at least 32 characters when live execution is enabled.")
    if settings.live_enabled:
        logger.warning("LIVE TRADING ACTIVE")
    await start_telegram_bot()
    _sync_task = asyncio.create_task(_account_sync_loop())


@app.on_event("shutdown")
async def shutdown() -> None:
    global _sync_task
    if _sync_task is not None:
        _sync_task.cancel()
        try:
            await _sync_task
        except asyncio.CancelledError:
            pass
        _sync_task = None
    await stop_telegram_bot()


async def _account_sync_loop() -> None:
    while True:
        try:
            await sync_live_account_state()
        except Exception as exc:
            logger.warning("Live account sync failed, will retry: %s", exc)
        await asyncio.sleep(30)


@app.get("/health", dependencies=[Depends(require_signed_request)])
def health() -> dict:
    settings = get_execution_settings()
    return {"status": "ok", "mode": "live" if settings.live_enabled else "paper", "exchange": settings.execution_exchange}


@app.post("/webhook/signal", response_model=SignalDecision)
async def receive_signal(signal: SignalIn, _signature: None = Depends(require_signed_request)) -> SignalDecision:
    return await process_signal(signal)


@app.get("/dashboard", dependencies=[Depends(require_signed_request)])
def execution_dashboard() -> dict:
    return dashboard()


@app.post("/pause", dependencies=[Depends(require_signed_request)])
def pause() -> dict:
    set_status(BotStatus.paused)
    return {"status": "paused"}


@app.post("/resume", dependencies=[Depends(require_signed_request)])
def resume() -> dict:
    set_status(BotStatus.active)
    return {"status": "active"}


@app.post("/kill", dependencies=[Depends(require_signed_request)])
def kill() -> dict:
    set_status(BotStatus.killed)
    return {"status": "killed"}
