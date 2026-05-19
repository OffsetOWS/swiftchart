from execution_bot.config import get_execution_settings
from execution_bot.exchanges.base import ExecutionExchange
from execution_bot.exchanges.hyperliquid import HyperliquidExecutionExchange
from execution_bot.exchanges.mock import MockExchange


def get_execution_exchange(name: str | None = None) -> ExecutionExchange:
    selected = (name or get_execution_settings().execution_exchange).lower()
    if selected == "hyperliquid":
        return HyperliquidExecutionExchange()
    if selected == "mock":
        return MockExchange()
    raise ValueError(f"Unsupported execution exchange: {selected}")
