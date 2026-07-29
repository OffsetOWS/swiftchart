from __future__ import annotations

import asyncio
import logging

from app.config import get_settings
from app.forex.lifecycle import update_forex_lifecycle
from app.forex.scanner import scan_forex

logger = logging.getLogger(__name__)
_task: asyncio.Task | None = None


async def _forex_worker() -> None:
    settings = get_settings()
    await asyncio.sleep(max(0, settings.forex_worker_startup_delay_seconds))
    scan_elapsed = settings.forex_scan_interval_seconds
    while True:
        try:
            await update_forex_lifecycle()
            scan_elapsed += settings.forex_lifecycle_interval_seconds
            if scan_elapsed >= settings.forex_scan_interval_seconds:
                result = await scan_forex()
                logger.info(
                    "Forex scheduled scan scan_id=%s created=%s reused=%s errors=%s",
                    result.scan_id,
                    len(result.created),
                    len(result.reused),
                    len(result.errors),
                )
                scan_elapsed = 0
        except Exception:
            logger.exception("Forex background worker failed")
        await asyncio.sleep(max(30, settings.forex_lifecycle_interval_seconds))


def start_forex_worker() -> None:
    global _task
    settings = get_settings()
    if not settings.forex_scanner_enabled or (_task and not _task.done()):
        return
    _task = asyncio.create_task(_forex_worker(), name="swiftchart-forex-worker")
