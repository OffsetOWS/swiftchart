from app.exchanges.hyperliquid import HyperliquidClient


def get_exchange(name: str):
    normalized = name.lower()
    if normalized in {"hyperliquid", "all"}:
        return HyperliquidClient()
    raise ValueError(f"Unsupported exchange: {name}")
