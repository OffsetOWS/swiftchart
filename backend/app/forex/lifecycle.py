from __future__ import annotations

from datetime import UTC, datetime
import logging

from app.forex.config import SUPPORTED_FOREX_PAIRS
from app.forex.market_data import ForexMarketDataService, forex_market_is_open
from app.forex.models import ForexSignalPlan
from app.forex.providers import ForexDataProvider
from app.forex.storage import list_signals, update_signal_market_state

logger = logging.getLogger(__name__)


def next_signal_status(
    signal: ForexSignalPlan,
    *,
    price: float,
    checked_at: datetime,
) -> tuple[str, datetime | None, datetime | None]:
    if signal.status == "PENDING_ENTRY":
        if checked_at >= signal.expires_at:
            return "EXPIRED", None, checked_at
        if signal.entry_low <= price <= signal.entry_high:
            return "OPEN", checked_at, None
        return signal.status, None, None

    if signal.status == "WAIT_FOR_RETEST":
        if checked_at >= signal.expires_at:
            return "EXPIRED", None, checked_at
        return signal.status, None, None

    if signal.direction == "LONG":
        stopped = price <= signal.stop_loss
        tp1_hit = price >= signal.take_profit_1
        tp2_hit = price >= signal.take_profit_2
    else:
        stopped = price >= signal.stop_loss
        tp1_hit = price <= signal.take_profit_1
        tp2_hit = price <= signal.take_profit_2

    if signal.status in {"OPEN", "TP1_HIT_TP2_RUNNING"}:
        if stopped:
            return "STOPPED", None, checked_at
        if tp2_hit:
            return "TP2_HIT", None, checked_at
        if tp1_hit:
            if signal.tp1_closes_position:
                return "TP1_HIT", None, checked_at
            return "TP1_HIT_TP2_RUNNING", None, None
    return signal.status, None, None


async def update_forex_lifecycle(provider: ForexDataProvider | None = None) -> list[ForexSignalPlan]:
    market_data = ForexMarketDataService(provider)
    checked_at = datetime.now(UTC).replace(microsecond=0)
    if not forex_market_is_open(checked_at):
        return []
    updated: list[ForexSignalPlan] = []
    for signal in list_signals(("WAIT_FOR_RETEST", "PENDING_ENTRY", "OPEN", "TP1_HIT_TP2_RUNNING"), limit=200):
        pair = SUPPORTED_FOREX_PAIRS.get(signal.symbol)
        if not pair:
            continue
        try:
            candles = await market_data.completed_candles(
                pair,
                signal.timeframe,
                limit=2,
                now=checked_at,
            )
            if candles.empty:
                continue
            price = float(candles["close"].iloc[-1])
            status, activated_at, closed_at = next_signal_status(
                signal,
                price=price,
                checked_at=checked_at,
            )
            updated.append(
                update_signal_market_state(
                    signal.id,
                    status=status,
                    price=price,
                    checked_at=checked_at,
                    activated_at=activated_at,
                    closed_at=closed_at,
                )
            )
        except Exception:
            logger.exception("Forex lifecycle update failed signal_id=%s", signal.id)
    return updated
