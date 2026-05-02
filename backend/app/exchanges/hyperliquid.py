from datetime import datetime, timezone

import httpx
import pandas as pd

from app.config import get_settings
from app.exchanges.base import ExchangeClient


TIMEFRAME_TO_HL = {
    "30m": "30m",
    "1h": "1h",
    "2h": "2h",
    "4h": "4h",
    "6h": "4h",
    "8h": "8h",
    "12h": "12h",
    "1d": "1d",
}


class HyperliquidClient(ExchangeClient):
    name = "hyperliquid"

    def __init__(self) -> None:
        self.base_url = get_settings().hyperliquid_base_url.rstrip("/")

    async def get_markets(self) -> list[dict]:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(f"{self.base_url}/info", json={"type": "meta"})
            response.raise_for_status()
        universe = response.json().get("universe", [])
        return [
            {
                "symbol": f"{item['name']}USDT",
                "base_asset": item["name"],
                "quote_asset": "USDT",
                "exchange": self.name,
            }
            for item in universe
        ]

    async def get_candles(self, symbol: str, timeframe: str, limit: int = 300) -> pd.DataFrame:
        coin = symbol.upper().replace("USDT", "")
        interval = TIMEFRAME_TO_HL.get(timeframe.lower(), "4h")
        now_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
        interval_minutes = {"30m": 30, "1h": 60, "2h": 120, "4h": 240, "8h": 480, "12h": 720, "1d": 1440}[interval]
        start_ms = now_ms - limit * interval_minutes * 60 * 1000
        payload = {
            "type": "candleSnapshot",
            "req": {"coin": coin, "interval": interval, "startTime": start_ms, "endTime": now_ms},
        }
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(f"{self.base_url}/info", json=payload)
            response.raise_for_status()

        rows = response.json()
        df = pd.DataFrame(rows)
        if df.empty:
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
        df = df.rename(columns={"t": "timestamp", "o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
        df["timestamp"] = df["timestamp"].apply(lambda value: datetime.fromtimestamp(value / 1000, tz=timezone.utc))
        for column in ["open", "high", "low", "close", "volume"]:
            df[column] = pd.to_numeric(df[column], errors="coerce")
        return df[["timestamp", "open", "high", "low", "close", "volume"]].dropna()
