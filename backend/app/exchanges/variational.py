from datetime import datetime, timezone
import logging
from typing import Any

import httpx
import pandas as pd

from app.config import get_settings
from app.exchanges.base import ExchangeClient, MarketDataUnavailable

logger = logging.getLogger(__name__)

TIMEFRAME_TO_VARIATIONAL = {
    "30m": "30m",
    "1h": "1h",
    "2h": "2h",
    "4h": "4h",
    "6h": "6h",
    "8h": "8h",
    "12h": "12h",
    "1d": "1d",
}


def _headers() -> dict[str, str]:
    key = get_settings().variational_api_key.strip()
    if not key:
        return {}
    return {"Authorization": f"Bearer {key}", "X-API-Key": key}


def _normalize_symbol(value: str) -> str:
    ticker = value.upper().strip()
    for suffix in ("-PERP", "_PERP", "PERP", "-USDC", "_USDC", "USDC", "-USD", "_USD", "USD", "-USDT", "_USDT", "USDT"):
        if ticker.endswith(suffix):
            ticker = ticker[: -len(suffix)]
            break
    ticker = "".join(ch for ch in ticker if ch.isalnum())
    return f"{ticker}USDT" if ticker else ""


def _rows_from_response(data: Any) -> list[Any]:
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []
    for key in ("candles", "data", "results", "bars", "ohlcv"):
        value = data.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            nested = _rows_from_response(value)
            if nested:
                return nested
    return []


def _timestamp(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        number = float(value)
        if number > 10_000_000_000:
            number = number / 1000
        return datetime.fromtimestamp(number, tz=timezone.utc)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            try:
                return _timestamp(float(value))
            except ValueError:
                return None
    return None


def normalize_candles(data: Any) -> pd.DataFrame:
    rows = []
    for row in _rows_from_response(data):
        if isinstance(row, dict):
            ts = _timestamp(row.get("timestamp") or row.get("time") or row.get("t") or row.get("start_time") or row.get("startTime"))
            values = {
                "timestamp": ts,
                "open": row.get("open", row.get("o")),
                "high": row.get("high", row.get("h")),
                "low": row.get("low", row.get("l")),
                "close": row.get("close", row.get("c")),
                "volume": row.get("volume", row.get("v", row.get("volume_24h", 0))),
            }
        elif isinstance(row, (list, tuple)) and len(row) >= 6:
            values = {
                "timestamp": _timestamp(row[0]),
                "open": row[1],
                "high": row[2],
                "low": row[3],
                "close": row[4],
                "volume": row[5],
            }
        else:
            continue
        rows.append(values)

    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
    for column in ["open", "high", "low", "close", "volume"]:
        df[column] = pd.to_numeric(df[column], errors="coerce")
    return df[["timestamp", "open", "high", "low", "close", "volume"]].dropna().sort_values("timestamp")


class VariationalClient(ExchangeClient):
    name = "variational"

    def __init__(self) -> None:
        settings = get_settings()
        self.base_url = settings.variational_api_base_url.rstrip("/")
        self.candles_path = settings.variational_candles_path if settings.variational_candles_path.startswith("/") else f"/{settings.variational_candles_path}"

    async def get_markets(self) -> list[dict]:
        if not get_settings().variational_enabled:
            return []
        try:
            async with httpx.AsyncClient(timeout=20, headers=_headers()) as client:
                response = await client.get(f"{self.base_url}/metadata/stats")
                response.raise_for_status()
                data = response.json()
        except Exception as exc:
            raise MarketDataUnavailable("Variational market metadata is temporarily unavailable.") from exc

        markets = []
        for listing in data.get("listings", []) if isinstance(data, dict) else []:
            ticker = str(listing.get("ticker") or listing.get("symbol") or "")
            symbol = _normalize_symbol(ticker)
            if not symbol:
                continue
            mark_price = listing.get("mark_price")
            try:
                active = float(mark_price) > 0
            except (TypeError, ValueError):
                active = False
            if not active:
                continue
            markets.append(
                {
                    "symbol": symbol,
                    "base_asset": symbol.removesuffix("USDT"),
                    "quote_asset": "USDT",
                    "exchange": self.name,
                    "active": True,
                    "volume": listing.get("volume_24h"),
                }
            )
        logger.info("Variational discovered %s active markets", len(markets))
        return markets

    async def get_candles(self, symbol: str, timeframe: str, limit: int = 300) -> pd.DataFrame:
        if not get_settings().variational_enabled:
            raise MarketDataUnavailable("Variational is disabled.")
        base_asset = symbol.upper().replace("USDT", "")
        interval = TIMEFRAME_TO_VARIATIONAL.get(timeframe.lower(), "4h")
        params = {
            "symbol": symbol.upper(),
            "ticker": base_asset,
            "market": base_asset,
            "interval": interval,
            "timeframe": interval,
            "limit": int(limit),
        }
        try:
            async with httpx.AsyncClient(timeout=20, headers=_headers()) as client:
                response = await client.get(f"{self.base_url}{self.candles_path}", params=params)
                response.raise_for_status()
                df = normalize_candles(response.json())
        except Exception as exc:
            raise MarketDataUnavailable(
                f"Variational candles unavailable for {symbol.upper()} {timeframe}. Configure VARIATIONAL_CANDLES_PATH if the endpoint differs."
            ) from exc
        if df.empty:
            raise MarketDataUnavailable(f"Variational returned no candles for {symbol.upper()} {timeframe}.")
        return df.tail(limit).reset_index(drop=True)
