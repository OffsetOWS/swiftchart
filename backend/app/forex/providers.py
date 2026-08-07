from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import UTC, datetime, timedelta
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


class ForexProviderQuotaExceeded(ForexProviderError):
    pass


class ForexDataProvider(ABC):
    name: str

    @abstractmethod
    async def candles(self, pair: ForexPairConfig, timeframe: str, limit: int = 240) -> pd.DataFrame:
        raise NotImplementedError


class TwelveDataForexProvider(ForexDataProvider):
    name = "twelvedata"
    interval_map = {"15m": "15min", "1h": "1h", "4h": "4h", "1d": "1day"}
    _unavailable_until: datetime | None = None
    _unavailable_reason: str | None = None

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key if api_key is not None else get_settings().twelve_data_api_key

    async def candles(self, pair: ForexPairConfig, timeframe: str, limit: int = 240) -> pd.DataFrame:
        if not self.api_key:
            raise ForexProviderNotConfigured("Forex data provider is not configured.")
        now = datetime.now(UTC)
        if self._unavailable_until and now < self._unavailable_until:
            raise ForexProviderQuotaExceeded(
                self._unavailable_reason or "Forex market-data capacity is temporarily exhausted."
            )
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
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        if response.status_code == 429:
            provider_message = str(payload.get("message") or "")
            daily_limit_hit = "credits for the day" in provider_message.lower()
            if daily_limit_hit:
                tomorrow = (now + timedelta(days=1)).date()
                self.__class__._unavailable_until = datetime.combine(
                    tomorrow,
                    datetime.min.time(),
                    tzinfo=UTC,
                )
                reason = "Forex market-data daily credit limit reached; scanning resumes after the provider reset."
            else:
                retry_after = max(int(response.headers.get("Retry-After", "60") or 60), 60)
                self.__class__._unavailable_until = now + timedelta(seconds=retry_after)
                reason = "Forex market-data rate limit reached; retry after the provider cooldown."
            self.__class__._unavailable_reason = reason
            raise ForexProviderQuotaExceeded(reason)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            message = str(payload.get("message") or f"HTTP {response.status_code}")
            raise ForexProviderError(f"Forex market-data request failed: {message}") from exc
        if payload.get("status") == "error":
            raise ForexProviderError(str(payload.get("message") or "Twelve Data request failed."))
        rows = []
        for item in payload.get("values") or []:
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
            except (KeyError, TypeError, ValueError) as exc:
                logger.debug("Skipped malformed Forex candle pair=%s error=%s", pair.pair, exc)
        if not rows:
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
        return pd.DataFrame(rows).sort_values("timestamp").reset_index(drop=True)


class FallbackForexProvider(ForexDataProvider):
    name = "oanda"

    def __init__(
        self,
        primary: ForexDataProvider,
        fallback: ForexDataProvider,
    ) -> None:
        self.primary = primary
        self.fallback = fallback
        self.last_provider_name = primary.name

    async def candles(
        self,
        pair: ForexPairConfig,
        timeframe: str,
        limit: int = 240,
    ) -> pd.DataFrame:
        try:
            result = await self.primary.candles(pair, timeframe, limit)
            self.last_provider_name = self.primary.name
            return result
        except ForexProviderError as primary_error:
            logger.warning(
                "Primary Forex provider failed provider=%s pair=%s timeframe=%s error=%s; "
                "using fallback=%s",
                self.primary.name,
                pair.pair,
                timeframe,
                primary_error,
                self.fallback.name,
            )
            try:
                result = await self.fallback.candles(pair, timeframe, limit)
                self.last_provider_name = self.fallback.name
                return result
            except ForexProviderError as fallback_error:
                raise ForexProviderError(
                    f"Primary provider unavailable ({primary_error}); "
                    f"fallback unavailable ({fallback_error})"
                ) from fallback_error


def get_forex_provider() -> ForexDataProvider:
    from app.forex.oanda import OandaForexProvider

    return FallbackForexProvider(
        primary=OandaForexProvider(),
        fallback=TwelveDataForexProvider(),
    )
