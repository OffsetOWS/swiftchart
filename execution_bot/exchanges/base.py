from __future__ import annotations

from abc import ABC, abstractmethod

from execution_bot.models import ExecutionPlan, MarketSnapshot


class ExecutionExchange(ABC):
    name: str

    @abstractmethod
    async def get_market_snapshot(self, symbol: str, timeframe: str, limit: int = 120) -> MarketSnapshot:
        raise NotImplementedError

    @abstractmethod
    async def place_order(self, plan: ExecutionPlan) -> dict:
        raise NotImplementedError

    @abstractmethod
    async def close_position(self, symbol: str, size: float) -> dict:
        raise NotImplementedError

    async def sync_account_balance(self) -> float | None:
        return None

    async def account_summary(self) -> dict:
        return {}

    async def recent_fills(self, symbol: str, start_time_ms: int) -> list[dict]:
        return []
