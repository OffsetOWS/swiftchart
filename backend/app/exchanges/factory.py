from app.exchanges.binance import BinanceClient
from app.exchanges.hyperliquid import HyperliquidClient


def get_exchange(name: str):
    normalized = name.lower()
    if normalized == "binance":
        return BinanceClient()
    if normalized == "hyperliquid":
        return HyperliquidClient()
    raise ValueError(f"Unsupported exchange: {name}")
