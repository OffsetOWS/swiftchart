from __future__ import annotations

import asyncio
import logging

from app.config import get_settings
from app.forex.config import enabled_forex_timeframes
from app.forex.lifecycle import update_forex_lifecycle
from app.forex.market_data import seconds_until_next_candle_close
from app.forex.scanner import scan_forex

logger = logging.getLogger(__name__)
_tasks: list[asyncio.Task] = []


async def _timeframe_worker(
    timeframe: str,
    startup_delay_seconds: int,
) -> None:
    await asyncio.sleep(max(0, startup_delay_seconds))
    while True:
        try:
            result = await scan_forex(timeframe=timeframe, trigger_source="scheduled")
            logger.info(
                "Forex scheduled scan timeframe=%s scan_id=%s created=%s reused=%s errors=%s",
                timeframe,
                result.scan_id,
                len(result.created),
                len(result.reused),
                len(result.errors),
            )
        except Exception:
            logger.exception("Forex timeframe worker failed timeframe=%s", timeframe)
        await asyncio.sleep(seconds_until_next_candle_close(timeframe))


async def _lifecycle_worker(interval_seconds: int, startup_delay_seconds: int) -> None:
    await asyncio.sleep(max(0, startup_delay_seconds))
    while True:
        try:
            await update_forex_lifecycle()
        except Exception:
            logger.exception("Forex lifecycle worker failed")
        await asyncio.sleep(max(30, interval_seconds))


def start_forex_worker() -> None:
    global _tasks
    settings = get_settings()
    if not settings.forex_scanner_enabled or any(not task.done() for task in _tasks):
        return
    base_delay = settings.forex_worker_startup_delay_seconds
    schedules = enabled_forex_timeframes()
    _tasks = [
        asyncio.create_task(
            _timeframe_worker(timeframe, base_delay + index * 5),
            name=f"swiftchart-forex-{timeframe.lower()}-worker",
        )
        for index, timeframe in enumerate(schedules)
    ]
    _tasks.append(
        asyncio.create_task(
            _lifecycle_worker(
                settings.forex_lifecycle_interval_seconds,
                base_delay + 5,
            ),
            name="swiftchart-forex-lifecycle-worker",
        )
    )
