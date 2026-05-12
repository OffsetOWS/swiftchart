from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone

import httpx

from app.config import get_settings
from app.models.schemas import TradeIdea

logger = logging.getLogger(__name__)

_sent_signal_ids: set[str] = set()


def execution_signal_id(idea: TradeIdea) -> str:
    entry = sum(idea.entry_zone) / 2
    raw = "|".join(
        [
            idea.exchange.lower(),
            idea.symbol.upper(),
            idea.timeframe.lower(),
            idea.direction.upper(),
            f"{entry:.8f}",
            f"{idea.stop_loss:.8f}",
            f"{idea.take_profit_1:.8f}",
        ]
    )
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]
    return f"swiftchart-{digest}"


def trade_idea_to_execution_signal(idea: TradeIdea) -> dict:
    entry = sum(idea.entry_zone) / 2
    return {
        "pair": idea.symbol.upper(),
        "side": "BUY" if idea.direction == "Long" else "SELL",
        "entry": entry,
        "confidence": idea.confidence_score,
        "timeframe": idea.timeframe,
        "reason": idea.reason[:1000],
        "signal_id": execution_signal_id(idea),
        "exchange": idea.exchange,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


async def dispatch_trade_ideas_to_execution(ideas: list[TradeIdea]) -> None:
    settings = get_settings()
    if not settings.execution_autotrade_enabled or not settings.execution_signal_webhook_url:
        return

    headers = {"Content-Type": "application/json"}
    if settings.execution_webhook_secret:
        headers["X-SwiftChart-Secret"] = settings.execution_webhook_secret

    async with httpx.AsyncClient(timeout=20) as client:
        for idea in ideas:
            payload = trade_idea_to_execution_signal(idea)
            signal_id = payload["signal_id"]
            if signal_id in _sent_signal_ids:
                continue
            try:
                response = await client.post(settings.execution_signal_webhook_url, json=payload, headers=headers)
                response.raise_for_status()
                decision = response.json()
                if decision.get("accepted"):
                    _sent_signal_ids.add(signal_id)
                logger.info(
                    "Execution signal dispatched symbol=%s side=%s accepted=%s reason=%s",
                    idea.symbol,
                    payload["side"],
                    decision.get("accepted"),
                    decision.get("reason"),
                )
            except Exception as exc:
                logger.exception("Could not dispatch execution signal symbol=%s timeframe=%s: %s", idea.symbol, idea.timeframe, exc)
