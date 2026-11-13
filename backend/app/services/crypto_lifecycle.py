from __future__ import annotations

import asyncio
import logging

from app.config import get_settings
from app.services.trade_history import check_trade_outcomes


logger = logging.getLogger(__name__)

_background_task: asyncio.Task | None = None


async def _crypto_lifecycle_loop() -> None:
    settings = get_settings()
    await asyncio.sleep(settings.crypto_lifecycle_startup_delay_seconds)

    while True:
        try:
            result = await check_trade_outcomes()
            logger.info(
                "Crypto lifecycle cycle completed checked=%s updated=%s errors=%s markets_fetched=%s",
                result.get("checked", 0),
                result.get("updated", 0),
                result.get("errors", 0),
                result.get("markets_fetched", 0),
            )
        except Exception:
            logger.exception("Crypto lifecycle cycle failed")

        await asyncio.sleep(settings.crypto_lifecycle_interval_seconds)


def start_crypto_lifecycle_worker() -> asyncio.Task | None:
    """Start one background crypto outcome evaluator for this API process."""

    global _background_task
    settings = get_settings()
    if not settings.crypto_lifecycle_enabled:
        logger.info("Crypto lifecycle worker is disabled")
        return None
    if _background_task is not None and not _background_task.done():
        return _background_task
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.warning("Crypto lifecycle worker was not started because no event loop is running")
        return None
    _background_task = loop.create_task(
        _crypto_lifecycle_loop(),
        name="swiftchart-crypto-lifecycle",
    )
    return _background_task
