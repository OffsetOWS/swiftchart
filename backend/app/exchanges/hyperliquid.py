from datetime import datetime, timezone
from functools import cached_property
import asyncio

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
        self.settings = get_settings()
        self.base_url = self.settings.hyperliquid_base_url.rstrip("/")

    async def _post_info(self, payload: dict) -> dict | list:
        async with httpx.AsyncClient(timeout=20) as client:
            for attempt in range(3):
                response = await client.post(f"{self.base_url}/info", json=payload)
                if response.status_code != 429:
                    response.raise_for_status()
                    return response.json()
                if attempt < 2:
                    await asyncio.sleep(0.6 * (attempt + 1))
            response.raise_for_status()
        return []

    @cached_property
    def configured_hip3_dexes(self) -> list[str]:
        return [item.strip() for item in self.settings.hyperliquid_hip3_dexes.split(",") if item.strip()]

    @staticmethod
    def _normalized_symbol(coin: str) -> str:
        return f"{coin.upper().replace('/', '').replace('-', '')}USDT"

    @staticmethod
    def _coin_from_symbol(symbol: str) -> tuple[str | None, str]:
        cleaned = symbol.strip().replace("/", "").replace("-", "")
        dex = None
        if ":" in cleaned:
            dex, cleaned = cleaned.split(":", 1)
        upper = cleaned.upper()
        coin = upper[:-4] if upper.endswith("USDT") else upper
        return dex, coin

    @staticmethod
    def _market_from_universe_item(item: dict, dex: str | None = None) -> dict:
        coin = str(item["name"])
        raw_symbol = coin if not dex or coin.lower().startswith(f"{dex.lower()}:") else f"{dex}:{coin}"
        symbol = f"{raw_symbol}USDT" if dex else HyperliquidClient._normalized_symbol(coin)
        display_symbol = f"{coin.upper()}USDT"
        return {
            "symbol": symbol,
            "display_symbol": display_symbol,
            "raw_symbol": raw_symbol,
            "base_asset": coin.upper(),
            "quote_asset": "USDT",
            "exchange": HyperliquidClient.name,
            "dex": dex,
            "is_hip3": bool(dex),
        }

    async def _meta(self, dex: str | None = None) -> dict:
        payload = {"type": "meta"}
        if dex:
            payload["dex"] = dex
        data = await self._post_info(payload)
        return data if isinstance(data, dict) else {}

    async def _perp_dexes(self) -> list[str]:
        dexes = list(self.configured_hip3_dexes)
        try:
            data = await self._post_info({"type": "perpDexs"})
        except Exception:
            return dexes

        if isinstance(data, dict):
            candidates = data.get("dexs") or data.get("perpDexs") or data.get("dexes") or []
        else:
            candidates = data

        for item in candidates:
            if isinstance(item, str):
                name = item
            elif isinstance(item, dict):
                name = item.get("name") or item.get("dex") or item.get("dexName")
            else:
                name = None
            if name and name not in dexes:
                dexes.append(str(name))
        return dexes

    async def get_markets(self) -> list[dict]:
        markets = [self._market_from_universe_item(item) for item in (await self._meta()).get("universe", [])]
        for dex in await self._perp_dexes():
            try:
                meta = await self._meta(dex)
                markets.extend(self._market_from_universe_item(item, dex=dex) for item in meta.get("universe", []))
            except Exception:
                continue
        return markets

    async def _resolve_coin(self, symbol: str) -> str:
        dex, coin = self._coin_from_symbol(symbol)
        if dex:
            return coin if coin.lower().startswith(f"{dex.lower()}:") else f"{dex}:{coin}"
        return coin

    async def get_candles(self, symbol: str, timeframe: str, limit: int = 300) -> pd.DataFrame:
        coin = await self._resolve_coin(symbol)
        interval = TIMEFRAME_TO_HL.get(timeframe.lower(), "4h")
        now_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
        interval_minutes = {"30m": 30, "1h": 60, "2h": 120, "4h": 240, "8h": 480, "12h": 720, "1d": 1440}[interval]
        start_ms = now_ms - limit * interval_minutes * 60 * 1000
        payload = {
            "type": "candleSnapshot",
            "req": {"coin": coin, "interval": interval, "startTime": start_ms, "endTime": now_ms},
        }
        rows = await self._post_info(payload)
        df = pd.DataFrame(rows)
        if df.empty:
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
        df = df.rename(columns={"t": "timestamp", "o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
        df["timestamp"] = df["timestamp"].apply(lambda value: datetime.fromtimestamp(value / 1000, tz=timezone.utc))
        for column in ["open", "high", "low", "close", "volume"]:
            df[column] = pd.to_numeric(df[column], errors="coerce")
        return df[["timestamp", "open", "high", "low", "close", "volume"]].dropna()
