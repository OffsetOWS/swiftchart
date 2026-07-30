from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import logging
import os
from typing import Any

import httpx
import pandas as pd

from app.config import get_settings
from app.forex.config import ForexPairConfig
from app.forex.providers import (
    ForexDataProvider,
    ForexProviderError,
    ForexProviderNotConfigured,
    ForexProviderQuotaExceeded,
)

logger = logging.getLogger(__name__)
startup_logger = logging.getLogger("uvicorn.error")

OANDA_GRANULARITIES = {
    "15m": "M15",
    "1h": "H1",
    "4h": "H4",
    "1d": "D",
    "15M": "M15",
    "1H": "H1",
    "4H": "H4",
    "1D": "D",
}

OANDA_INSTRUMENTS = {
    "EURUSD": "EUR_USD",
    "GBPUSD": "GBP_USD",
    "USDJPY": "USD_JPY",
    "AUDUSD": "AUD_USD",
    "NZDUSD": "NZD_USD",
    "USDCAD": "USD_CAD",
    "USDCHF": "USD_CHF",
    "EURGBP": "EUR_GBP",
    "EURJPY": "EUR_JPY",
    "GBPJPY": "GBP_JPY",
    "XAUUSD": "XAU_USD",
}


def oanda_instrument(symbol: str) -> str:
    normalized = str(symbol or "").upper().replace("/", "").replace("-", "").replace("_", "")
    try:
        return OANDA_INSTRUMENTS[normalized]
    except KeyError as exc:
        raise ForexProviderError(f"Unsupported OANDA Forex instrument: {symbol}") from exc


class OandaForexProvider(ForexDataProvider):
    name = "oanda"
    _unavailable_until: datetime | None = None
    _last_successful_request: datetime | None = None
    _last_error: str | None = None

    def __init__(
        self,
        *,
        api_key: str | None = None,
        account_id: str | None = None,
        environment: str | None = None,
        base_url: str | None = None,
        timeout_seconds: float | None = None,
        retry_attempts: int | None = None,
        retry_backoff_seconds: float | None = None,
    ) -> None:
        settings = get_settings()
        self.api_key = (
            api_key
            if api_key is not None
            else settings.oanda_api_key or os.getenv("OANDA_API_TOKEN", "")
        )
        self.account_id = (
            account_id if account_id is not None else settings.oanda_account_id
        )
        self.environment = (
            environment
            if environment is not None
            else os.getenv("OANDA_ENV")
            or os.getenv("OANDA_ENVIRONMENT")
            or settings.oanda_env
        ).strip().lower()
        self.base_url = (
            base_url if base_url is not None else settings.oanda_base_url
        ).rstrip("/")
        self.timeout_seconds = (
            timeout_seconds
            if timeout_seconds is not None
            else settings.oanda_request_timeout_seconds
        )
        self.retry_attempts = (
            retry_attempts
            if retry_attempts is not None
            else settings.oanda_retry_attempts
        )
        self.retry_backoff_seconds = (
            retry_backoff_seconds
            if retry_backoff_seconds is not None
            else settings.oanda_retry_backoff_seconds
        )
    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.account_id and self.base_url)

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Accept-Datetime-Format": "RFC3339",
            "Content-Type": "application/json",
        }

    async def _request(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self.configured:
            raise ForexProviderNotConfigured("OANDA Forex provider is not configured.")
        now = datetime.now(UTC)
        if self._unavailable_until and now < self._unavailable_until:
            raise ForexProviderQuotaExceeded(
                "OANDA Forex provider is temporarily rate limited."
            )

        last_error: Exception | None = None
        for attempt in range(self.retry_attempts):
            try:
                async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                    response = await client.get(
                        f"{self.base_url}{path}",
                        params=params,
                        headers=self.headers,
                    )
                try:
                    payload = response.json()
                except ValueError:
                    payload = {}

                if response.status_code == 429:
                    retry_after = max(
                        int(response.headers.get("Retry-After", "60") or 60),
                        1,
                    )
                    self.__class__._unavailable_until = now + timedelta(
                        seconds=retry_after
                    )
                    raise ForexProviderQuotaExceeded(
                        "OANDA Forex provider rate limit reached."
                    )
                if response.status_code in {401, 403}:
                    self.__class__._unavailable_until = now + timedelta(minutes=5)
                    raise ForexProviderError(
                        "OANDA authentication failed; verify the API token, account, and environment."
                    )
                if response.status_code >= 500:
                    raise httpx.HTTPStatusError(
                        "OANDA service unavailable.",
                        request=response.request,
                        response=response,
                    )
                try:
                    response.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    detail = str(
                        payload.get("errorMessage")
                        or payload.get("errorCode")
                        or f"HTTP {response.status_code}"
                    )
                    raise ForexProviderError(
                        f"OANDA market-data request failed: {detail}"
                    ) from exc

                self.__class__._last_successful_request = datetime.now(UTC)
                self.__class__._last_error = None
                self.__class__._unavailable_until = None
                return payload
            except (ForexProviderNotConfigured, ForexProviderQuotaExceeded):
                raise
            except ForexProviderError as exc:
                self.__class__._last_error = str(exc)
                raise
            except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError) as exc:
                last_error = exc
                if attempt + 1 >= self.retry_attempts:
                    break
                await asyncio.sleep(self.retry_backoff_seconds * (2**attempt))

        self.__class__._last_error = (
            f"OANDA request failed after {self.retry_attempts} attempts: "
            f"{type(last_error).__name__ if last_error else 'unknown error'}"
        )
        raise ForexProviderError(self.__class__._last_error)

    async def candles(
        self,
        pair: ForexPairConfig,
        timeframe: str,
        limit: int = 240,
    ) -> pd.DataFrame:
        try:
            granularity = OANDA_GRANULARITIES[timeframe]
        except KeyError as exc:
            raise ForexProviderError(
                f"Unsupported OANDA Forex timeframe: {timeframe}"
            ) from exc
        instrument = oanda_instrument(pair.pair)
        payload = await self._request(
            f"/v3/instruments/{instrument}/candles",
            params={
                "price": "M",
                "granularity": granularity,
                "count": min(max(limit, 1), 5000),
            },
        )
        rows: list[dict[str, Any]] = []
        for candle in payload.get("candles") or []:
            if candle.get("complete") is False:
                continue
            midpoint = candle.get("mid") or {}
            try:
                timestamp = datetime.fromisoformat(
                    str(candle["time"]).replace("Z", "+00:00")
                )
                rows.append(
                    {
                        "timestamp": timestamp.astimezone(UTC),
                        "open": float(midpoint["o"]),
                        "high": float(midpoint["h"]),
                        "low": float(midpoint["l"]),
                        "close": float(midpoint["c"]),
                        "volume": float(candle.get("volume") or 0),
                    }
                )
            except (KeyError, TypeError, ValueError) as exc:
                logger.debug(
                    "Skipped malformed OANDA candle pair=%s error=%s",
                    pair.pair,
                    type(exc).__name__,
                )
        if not rows:
            return pd.DataFrame(
                columns=["timestamp", "open", "high", "low", "close", "volume"]
            )
        return pd.DataFrame(rows).sort_values("timestamp").reset_index(drop=True)

    async def health(self) -> dict[str, Any]:
        connected = False
        if self.configured:
            try:
                await self._request(f"/v3/accounts/{self.account_id}/summary")
                connected = True
            except ForexProviderError as exc:
                self.__class__._last_error = str(exc)
        return {
            "provider": self.name,
            "configured": self.configured,
            "connected": connected,
            "account_id": self.account_id or None,
            "environment": self.environment,
            "last_successful_request": self.__class__._last_successful_request,
            "last_error": self.__class__._last_error,
        }


async def verify_oanda_startup() -> None:
    provider = OandaForexProvider()
    if not provider.configured:
        startup_logger.warning(
            "OANDA startup verification skipped: provider is not configured."
        )
        return
    from app.forex.config import SUPPORTED_FOREX_PAIRS

    try:
        frame = await provider.candles(
            SUPPORTED_FOREX_PAIRS["EURUSD"],
            "1h",
            2,
        )
        if frame.empty:
            startup_logger.warning(
                "OANDA startup verification returned no complete EUR_USD H1 candle."
            )
            return
        candle = frame.iloc[-1]
        startup_logger.info(
            "OANDA startup verification succeeded instrument=EUR_USD granularity=H1 "
            "time=%s open=%s high=%s low=%s close=%s",
            candle["timestamp"],
            candle["open"],
            candle["high"],
            candle["low"],
            candle["close"],
        )
    except ForexProviderError as exc:
        startup_logger.warning("OANDA startup verification failed: %s", exc)
