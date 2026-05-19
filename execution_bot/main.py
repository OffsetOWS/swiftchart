from __future__ import annotations

import asyncio
import logging

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from execution_bot.config import get_execution_settings
from execution_bot.models import BotStatus, SignalDecision, SignalIn
from execution_bot.service import process_signal, sync_live_account_state
from execution_bot.storage import dashboard, init_db, set_status
from execution_bot.telegram_bot import start_telegram_bot, stop_telegram_bot

app = FastAPI(title="SwiftChart Execution Bot")
logger = logging.getLogger(__name__)
_sync_task: asyncio.Task | None = None

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup() -> None:
    global _sync_task
    init_db()
    settings = get_execution_settings()
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


def verify_secret(x_swiftchart_secret: str | None = Header(default=None)) -> None:
    settings = get_execution_settings()
    if settings.execution_webhook_secret and x_swiftchart_secret != settings.execution_webhook_secret:
        raise HTTPException(status_code=401, detail="Invalid execution webhook secret.")


@app.get("/health")
def health() -> dict:
    settings = get_execution_settings()
    return {"status": "ok", "mode": "live" if settings.live_enabled else "paper", "exchange": settings.execution_exchange}


@app.post("/webhook/signal", response_model=SignalDecision)
async def receive_signal(signal: SignalIn, _secret: None = Depends(verify_secret)) -> SignalDecision:
    return await process_signal(signal)


@app.get("/dashboard")
def execution_dashboard() -> dict:
    return dashboard()


@app.post("/pause")
def pause() -> dict:
    set_status(BotStatus.paused)
    return {"status": "paused"}


@app.post("/resume")
def resume() -> dict:
    set_status(BotStatus.active)
    return {"status": "active"}


@app.post("/kill")
def kill() -> dict:
    set_status(BotStatus.killed)
    return {"status": "killed"}
