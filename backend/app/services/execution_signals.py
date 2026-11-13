from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
import uuid
from datetime import datetime, timezone

import httpx

from app.config import get_settings
from app.models.schemas import TradeIdea
from app.services.alert_dedupe import mark_alert_sent, opportunity_dedupe_key, should_skip_alert

logger = logging.getLogger(__name__)

_sent_signal_ids: set[str] = set()


def execution_signal_id(idea: TradeIdea) -> str:
    entry = sum(idea.entry_zone) / 2
    raw = opportunity_dedupe_key(idea) or "|".join(
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
        "source": idea.source or idea.exchange,
        "move_maturity": idea.move_maturity,
        "exhaustion_risk": idea.exhaustion_risk,
        "entry_status": idea.entry_status,
        "strategy_family": idea.setup_family,
        "strategy_version": idea.strategy_version,
        "edge_status": idea.edge_status,
        "strategy_decision": idea.strategy_decision,
        "downgraded_reasons": idea.downgraded_reasons,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def signed_execution_headers(secret: str, body: bytes) -> dict[str, str]:
    timestamp = str(int(time.time()))
    nonce = uuid.uuid4().hex
    payload = f"{timestamp}.{nonce}.".encode("utf-8") + body
    signature = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return {
        "X-SwiftChart-Timestamp": timestamp,
        "X-SwiftChart-Nonce": nonce,
        "X-SwiftChart-Signature": signature,
    }


async def dispatch_trade_ideas_to_execution(ideas: list[TradeIdea]) -> None:
    settings = get_settings()
    if not settings.execution_autotrade_enabled or not settings.execution_signal_webhook_url:
        return

    async with httpx.AsyncClient(timeout=20) as client:
        for idea in ideas:
            if idea.strategy_version is not None and idea.strategy_decision != "TRADE":
                logger.info(
                    "V2 execution signal skipped symbol=%s timeframe=%s strategy=%s:%s decision=%s edge_status=%s",
                    idea.symbol,
                    idea.timeframe,
                    idea.setup_family,
                    idea.strategy_version,
                    idea.strategy_decision,
                    idea.edge_status,
                )
                continue
            if should_skip_alert(idea, namespace="execution"):
                continue
            if idea.entry_status != "READY":
                logger.info(
                    "Execution signal skipped symbol=%s timeframe=%s status=%s exhaustion=%s",
                    idea.symbol,
                    idea.timeframe,
                    idea.entry_status,
                    idea.exhaustion_risk,
                )
                continue
            payload = trade_idea_to_execution_signal(idea)
            signal_id = payload["signal_id"]
            if signal_id in _sent_signal_ids:
                continue
            try:
                body = json.dumps(payload, separators=(",", ":"), default=str).encode("utf-8")
                headers = {"Content-Type": "application/json"}
                if settings.execution_webhook_secret:
                    headers.update(signed_execution_headers(settings.execution_webhook_secret, body))
                response = await client.post(settings.execution_signal_webhook_url, content=body, headers=headers)
                response.raise_for_status()
                decision = response.json()
                if decision.get("accepted"):
                    _sent_signal_ids.add(signal_id)
                    mark_alert_sent(idea, namespace="execution")
                logger.info(
                    "Execution signal dispatched symbol=%s side=%s accepted=%s reason=%s",
                    idea.symbol,
                    payload["side"],
                    decision.get("accepted"),
                    decision.get("reason"),
                )
            except Exception as exc:
                logger.exception("Could not dispatch execution signal symbol=%s timeframe=%s: %s", idea.symbol, idea.timeframe, exc)
