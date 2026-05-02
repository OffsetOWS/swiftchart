from datetime import datetime, timezone

import httpx
import pandas as pd

from app.config import get_settings
from app.exchanges.base import ExchangeClient


class BinanceClient(ExchangeClient):
    name = "binance"

    def __init__(self) -> None:
        self.base_url = get_settings().binance_base_url.rstrip("/")

    async def get_markets(self) -> list[dict]:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(f"{self.base_url}/api/v3/exchangeInfo")
            response.raise_for_status()
        symbols = response.json().get("symbols", [])
        return [
            {
                "symbol": item["symbol"],
                "base_asset": item["baseAsset"],
                "quote_asset": item["quoteAsset"],
                "exchange": self.name,
            }
            for item in symbols
            if item.get("status") == "TRADING" and item.get("quoteAsset") == "USDT"
        ]

    async def get_candles(self, symbol: str, timeframe: str, limit: int = 300) -> pd.DataFrame:
        params = {"symbol": symbol.upper(), "interval": timeframe.lower(), "limit": limit}
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(f"{self.base_url}/api/v3/klines", params=params)
            response.raise_for_status()

        rows = response.json()
        df = pd.DataFrame(
            rows,
            columns=[
                "open_time",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "close_time",
                "quote_volume",
                "trades",
                "taker_buy_base",
                "taker_buy_quote",
                "ignore",
            ],
        )
        df = df[["open_time", "open", "high", "low", "close", "volume"]].copy()
        df["timestamp"] = df["open_time"].apply(lambda value: datetime.fromtimestamp(value / 1000, tz=timezone.utc))
        for column in ["open", "high", "low", "close", "volume"]:
            df[column] = pd.to_numeric(df[column], errors="coerce")
        return df[["timestamp", "open", "high", "low", "close", "volume"]].dropna()
