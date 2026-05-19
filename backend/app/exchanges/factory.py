from app.exchanges.hyperliquid import HyperliquidClient
from app.exchanges.variational import VariationalClient


def get_exchange(name: str):
    normalized = name.lower()
    if normalized in {"hyperliquid", "all"}:
        return HyperliquidClient()
    if normalized == "variational":
        return VariationalClient()
    raise ValueError(f"Unsupported exchange: {name}")
