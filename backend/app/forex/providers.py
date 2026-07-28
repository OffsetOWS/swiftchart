from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import UTC, datetime
import logging

import httpx
import pandas as pd

from app.config import get_settings
from app.forex.config import ForexPairConfig

logger = logging.getLogger(__name__)


class ForexProviderError(Exception):
    pass


class ForexProviderNotConfigured(ForexProviderError):
    pass


class ForexDataProvider(ABC):
    name: str

    @abstractmethod
    async def candles(self, pair: ForexPairConfig, timeframe: str, limit: int = 240) -> pd.DataFrame:
        raise NotImplementedError


class TwelveDataForexProvider(ForexDataProvider):
    name = "twelvedata"
    interval_map = {
        "15m": "15min",
        "1h": "1h",
        "4h": "4h",
        "1d": "1day",
    }

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key if api_key is not None else get_settings().twelve_data_api_key

    async def candles(self, pair: ForexPairConfig, timeframe: str, limit: int = 240) -> pd.DataFrame:
        if not self.api_key:
            raise ForexProviderNotConfigured("Forex data provider is not configured.")
        interval = self.interval_map.get(timeframe.lower())
        if not interval:
            raise ForexProviderError(f"Unsupported forex timeframe: {timeframe}")
        params = {
            "symbol": pair.provider_symbol,
            "interval": interval,
            "outputsize": min(max(limit, 1), 5000),
            "apikey": self.api_key,
        }
        async with httpx.AsyncClient(timeout=18) as client:
            response = await client.get("https://api.twelvedata.com/time_series", params=params)
            response.raise_for_status()
            payload = response.json()
        if payload.get("status") == "error":
            raise ForexProviderError(str(payload.get("message") or "Twelve Data request failed."))
        values = payload.get("values") or []
        rows = []
        for item in values:
            try:
                timestamp = datetime.fromisoformat(str(item["datetime"]).replace("Z", "+00:00"))
                timestamp = timestamp if timestamp.tzinfo else timestamp.replace(tzinfo=UTC)
                rows.append(
                    {
                        "timestamp": timestamp.astimezone(UTC),
                        "open": float(item["open"]),
                        "high": float(item["high"]),
                        "low": float(item["low"]),
                        "close": float(item["close"]),
                        "volume": float(item.get("volume") or 0),
                    }
                )
            except Exception as exc:
                logger.debug("Skipped malformed forex candle pair=%s item=%s error=%s", pair.pair, item, exc)
        if not rows:
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
        return pd.DataFrame(rows).sort_values("timestamp").reset_index(drop=True)


def get_forex_provider() -> ForexDataProvider:
    return TwelveDataForexProvider()
