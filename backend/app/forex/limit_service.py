from __future__ import annotations

from datetime import UTC, datetime
import logging

import pandas as pd

from app.config import get_settings
from app.forex.config import SUPPORTED_FOREX_PAIRS
from app.forex.context import evaluate_cross_market_context
from app.forex.limit_lifecycle import advance_limit_opportunity
from app.forex.limit_storage import (
    insert_limit_opportunity,
    list_limit_opportunities,
    update_limit_opportunity,
)
from app.forex.limit_strategy import detect_liquidity_sweep_fvg_limit
from app.forex.market_data import ForexMarketDataService
from app.forex.news import forex_news_risk
from app.forex.sessions import forex_session_state
from app.forex.providers import ForexDataProvider
from app.forex.telegram import enqueue_forex_limit_event

logger = logging.getLogger(__name__)
LIMIT_SCAN_TIMEFRAMES = ("1H", "4H", "1D")


def _htf_timeframe(timeframe: str) -> str:
    return {"1H": "4H", "4H": "1D", "1D": "1D"}[timeframe]


def _bias(frame: pd.DataFrame) -> str:
    if frame is None or len(frame) < 30:
        return "NEUTRAL"
    close = frame["close"].astype(float)
    fast = close.ewm(span=20, adjust=False).mean()
    slow = close.ewm(span=50, adjust=False).mean()
    if close.iloc[-1] > fast.iloc[-1] > slow.iloc[-1]:
        return "BULLISH"
    if close.iloc[-1] < fast.iloc[-1] < slow.iloc[-1]:
        return "BEARISH"
    if fast.iloc[-1] > fast.iloc[-5]:
        return "TRANSITIONING_BULLISH"
    if fast.iloc[-1] < fast.iloc[-5]:
        return "TRANSITIONING_BEARISH"
    return "NEUTRAL"


async def scan_limit_opportunities(
    provider: ForexDataProvider | None = None,
    *,
    timeframe: str = "1H",
    now: datetime | None = None,
) -> dict:
    settings = get_settings()
    timeframe = timeframe.upper()
    if timeframe not in LIMIT_SCAN_TIMEFRAMES:
        raise ValueError(f"Unsupported limit-strategy timeframe: {timeframe}")
    scan_enabled = (
        settings.forex_liquidity_fvg_limit_enabled
        or settings.forex_liquidity_fvg_limit_shadow_mode
    )
    if not scan_enabled:
        return {"enabled": False, "shadow_mode": settings.forex_liquidity_fvg_limit_shadow_mode, "created": [], "reused": [], "rejected": []}
    if settings.forex_liquidity_fvg_auto_execution_enabled and settings.forex_liquidity_fvg_limit_shadow_mode:
        raise RuntimeError("Shadow-mode limit opportunities cannot enable broker execution.")
    if settings.forex_liquidity_fvg_auto_execution_enabled:
        raise RuntimeError("Liquidity Sweep + FVG auto execution is intentionally unsupported.")

    evaluated_at = (now or datetime.now(UTC)).astimezone(UTC).replace(microsecond=0)
    market_data = ForexMarketDataService(provider)
    session_label = forex_session_state(evaluated_at).active_session
    created, reused, rejected = [], [], []
    for pair in SUPPORTED_FOREX_PAIRS.values():
        try:
            news_risk, _ = forex_news_risk(pair.pair)
            candles = await market_data.completed_candles(pair, timeframe, limit=140, now=evaluated_at)
            htf = await market_data.completed_candles(pair, _htf_timeframe(timeframe), limit=100, now=evaluated_at)
            opportunity, reason = detect_liquidity_sweep_fvg_limit(
                pair, candles, timeframe=timeframe, htf_bias=_bias(htf),
                news_risk=news_risk, now=evaluated_at,
                session_label=session_label,
            )
            if opportunity is None:
                rejected.append({"pair": pair.pair, "reason": reason})
                continue
            context = await evaluate_cross_market_context(
                pair.pair, opportunity.direction, timeframe, market_data, now=evaluated_at
            )
            opportunity = opportunity.model_copy(
                update={
                    "context": context,
                    "final_score": max(0, min(100, round(opportunity.technical_score + context.total_adjustment, 1))),
                }
            )
            persisted, was_created = insert_limit_opportunity(opportunity)
            (created if was_created else reused).append(persisted)
            if was_created and not persisted.shadow_mode:
                enqueue_forex_limit_event(persisted, "OPPORTUNITY")
        except Exception as exc:
            logger.exception("Limit strategy scan failed pair=%s timeframe=%s", pair.pair, timeframe)
            rejected.append({"pair": pair.pair, "reason": type(exc).__name__})
    return {
        "enabled": True,
        "shadow_mode": settings.forex_liquidity_fvg_limit_shadow_mode,
        "created": [item.model_dump(mode="json") for item in created],
        "reused": [item.model_dump(mode="json") for item in reused],
        "rejected": rejected,
    }


async def update_limit_lifecycle(
    provider: ForexDataProvider | None = None,
    *,
    now: datetime | None = None,
) -> list:
    checked_at = (now or datetime.now(UTC)).astimezone(UTC).replace(microsecond=0)
    market_data = ForexMarketDataService(provider)
    tracked = list_limit_opportunities(
        ("WAIT_FOR_RETEST", "PENDING_LIMIT", "ACTIVE_TRADE", "TP1_HIT_TP2_RUNNING"), limit=500
    )
    updated = []
    for opportunity in tracked:
        pair = SUPPORTED_FOREX_PAIRS.get(opportunity.pair)
        if not pair:
            continue
        try:
            news_risk, _ = forex_news_risk(opportunity.pair)
            candles = await market_data.completed_candles(
                pair, opportunity.timeframe, limit=2, now=checked_at
            )
            if candles.empty:
                continue
            candle = candles.iloc[-1]
            advanced = advance_limit_opportunity(
                opportunity,
                candle_high=float(candle["high"]), candle_low=float(candle["low"]),
                candle_close=float(candle["close"]), candle_time=pd.Timestamp(candle["timestamp"]).to_pydatetime(),
                news_lockout=news_risk == "HIGH",
            )
            if advanced != opportunity:
                update_limit_opportunity(advanced)
                if (
                    advanced.opportunity_status != opportunity.opportunity_status
                    and not advanced.shadow_mode
                ):
                    enqueue_forex_limit_event(advanced, advanced.opportunity_status)
                updated.append(advanced)
        except Exception:
            logger.exception("Limit lifecycle failed opportunity_id=%s", opportunity.id)
    return updated
