from __future__ import annotations

from datetime import datetime, timedelta, timezone

from execution_bot.exchanges.base import ExecutionExchange
from execution_bot.models import Candle, ExecutionPlan, MarketSnapshot


class MockExchange(ExecutionExchange):
    name = "mock"

    async def get_market_snapshot(self, symbol: str, timeframe: str, limit: int = 120) -> MarketSnapshot:
        candles: list[Candle] = []
        price = 100.0
        now = datetime.now(timezone.utc)
        for index in range(limit):
            drift = 0.18 if index % 7 else -0.08
            price += drift
            candles.append(
                Candle(
                    timestamp=now - timedelta(minutes=(limit - index) * 15),
                    open=price - 0.25,
                    high=price + 0.75,
                    low=price - 0.85,
                    close=price,
                    volume=1000 + index * 3,
                )
            )
        return MarketSnapshot(candles=candles, bid=price * 0.9995, ask=price * 1.0005)

    async def place_order(self, plan: ExecutionPlan) -> dict:
        return {
            "id": f"paper-{plan.symbol}-{int(datetime.now(timezone.utc).timestamp())}",
            "status": "accepted",
            "mode": plan.mode.value,
        }

    async def close_position(self, symbol: str, size: float) -> dict:
        return {"id": f"paper-close-{symbol}", "status": "accepted", "size": size}

    async def sync_account_balance(self) -> float | None:
        return None

    async def account_summary(self) -> dict:
        return {"balance": None, "positions": []}

    async def recent_fills(self, symbol: str, start_time_ms: int) -> list[dict]:
        return []
