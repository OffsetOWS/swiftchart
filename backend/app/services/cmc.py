from __future__ import annotations

import asyncio
import logging
import math
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)


@dataclass
class CacheEntry:
    expires_at: float
    value: Any


_cache: dict[str, CacheEntry] = {}
_cache_lock = asyncio.Lock()


def market_quality_score(market_cap: float, volume_24h: float, cmc_rank: int | None) -> int:
    """Informational CMC quality score. It is never used by SwiftChart signal logic."""
    cap_score = min(1.0, max(0.0, math.log10(max(market_cap, 1)) / 12))
    volume_score = min(1.0, max(0.0, math.log10(max(volume_24h, 1)) / 11))
    rank_score = max(0.0, 1.0 - ((max(cmc_rank or 500, 1) - 1) / 499))
    return round((cap_score * 0.45 + volume_score * 0.35 + rank_score * 0.20) * 100)


def quality_label(score: int) -> str:
    if score >= 75:
        return "High Quality Asset"
    if score >= 55:
        return "Established Market"
    return "Lower Liquidity Risk"


def _base_symbol(symbol: str) -> str:
    normalized = str(symbol or "").strip().upper()
    for suffix in ("USDT", "USDC", "USD", "PERP"):
        if normalized.endswith(suffix) and len(normalized) > len(suffix):
            return normalized[: -len(suffix)]
    return normalized


def _normalize_asset(item: dict[str, Any]) -> dict[str, Any] | None:
    quote = (item.get("quote") or {}).get("USD") or {}
    symbol = _base_symbol(item.get("symbol", ""))
    if not symbol:
        return None
    market_cap = float(quote.get("market_cap") or 0)
    volume_24h = float(quote.get("volume_24h") or 0)
    rank = item.get("cmc_rank")
    rank = int(rank) if rank is not None else None
    score = market_quality_score(market_cap, volume_24h, rank)
    return {
        "id": item.get("id"),
        "name": item.get("name") or symbol,
        "symbol": symbol,
        "price": float(quote.get("price") or 0),
        "market_cap": market_cap,
        "volume_24h": volume_24h,
        "price_change_24h": float(quote.get("percent_change_24h") or 0),
        "cmc_rank": rank,
        "market_quality_score": score,
        "quality_label": quality_label(score),
    }


async def _cached_get(path: str, params: dict[str, Any]) -> Any:
    settings = get_settings()
    cache_key = f"{path}:{sorted(params.items())}"
    now = time.monotonic()
    cached = _cache.get(cache_key)
    if cached and cached.expires_at > now:
        return cached.value

    headers = {
        "Accept": "application/json",
        "X-CMC_PRO_API_KEY": settings.cmc_api_key,
    }
    async with httpx.AsyncClient(
        base_url=settings.cmc_api_base_url.rstrip("/"),
        timeout=settings.cmc_request_timeout_seconds,
        headers=headers,
    ) as client:
        response = await client.get(path, params=params)
        response.raise_for_status()
        payload = response.json().get("data", [])

    async with _cache_lock:
        _cache[cache_key] = CacheEntry(
            expires_at=now + settings.cmc_cache_ttl_seconds,
            value=payload,
        )
    return payload


async def _safe_get(path: str, params: dict[str, Any], default: Any) -> Any:
    try:
        return await _cached_get(path, params)
    except Exception as exc:
        logger.warning("CoinMarketCap context unavailable for %s: %s", path, type(exc).__name__)
        return default


def _asset_list(payload: Any) -> list[dict[str, Any]]:
    values = payload.values() if isinstance(payload, dict) else payload
    assets_by_symbol: dict[str, dict[str, Any]] = {}
    for item in values or []:
        if isinstance(item, list):
            candidates = item
        else:
            candidates = [item]
        for candidate in candidates:
            normalized = _normalize_asset(candidate)
            if normalized:
                symbol = normalized["symbol"]
                current = assets_by_symbol.get(symbol)
                candidate_key = (
                    normalized["cmc_rank"] is not None,
                    -(normalized["cmc_rank"] or 10**9),
                    normalized["market_cap"],
                    normalized["volume_24h"],
                )
                current_key = (
                    current["cmc_rank"] is not None,
                    -(current["cmc_rank"] or 10**9),
                    current["market_cap"],
                    current["volume_24h"],
                ) if current else None
                if current is None or candidate_key > current_key:
                    assets_by_symbol[symbol] = normalized
    return list(assets_by_symbol.values())


async def get_market_intelligence(symbols: list[str] | None = None) -> dict[str, Any]:
    settings = get_settings()
    if not settings.cmc_api_key:
        return {
            "available": False,
            "source": "CoinMarketCap",
            "reason": "not_configured",
            "assets": {},
            "trending": [],
            "top_gainers": [],
            "top_losers": [],
            "highest_quality": [],
        }

    requested = sorted({_base_symbol(symbol) for symbol in (symbols or []) if _base_symbol(symbol)})
    quote_params = {"symbol": ",".join(requested), "convert": "USD", "skip_invalid": "true"} if requested else None

    tasks = [
        _safe_get(
            "/v1/cryptocurrency/listings/latest",
            {"start": 1, "limit": 100, "convert": "USD", "sort": "market_cap", "sort_dir": "desc"},
            [],
        ),
        _safe_get(
            "/v1/cryptocurrency/trending/latest",
            {"start": 1, "limit": 10, "convert": "USD"},
            [],
        ),
        _safe_get(
            "/v1/cryptocurrency/trending/gainers-losers",
            {"start": 1, "limit": 20, "convert": "USD", "time_period": "24h"},
            [],
        ),
    ]
    if quote_params:
        tasks.append(_safe_get("/v2/cryptocurrency/quotes/latest", quote_params, {}))

    results = await asyncio.gather(*tasks)
    listings = _asset_list(results[0])
    trending = _asset_list(results[1])
    movers = _asset_list(results[2])
    quotes = _asset_list(results[3]) if quote_params else []

    all_assets = {asset["symbol"]: asset for asset in listings}
    all_assets.update({asset["symbol"]: asset for asset in quotes})
    if not trending:
        trending = sorted(
            listings,
            key=lambda asset: abs(asset["price_change_24h"]) * math.log10(max(asset["volume_24h"], 10)),
            reverse=True,
        )[:5]
    mover_source = movers or listings
    top_gainers = sorted(mover_source, key=lambda asset: asset["price_change_24h"], reverse=True)[:5]
    top_losers = sorted(mover_source, key=lambda asset: asset["price_change_24h"])[:5]
    highest_quality = sorted(listings, key=lambda asset: asset["market_quality_score"], reverse=True)[:5]
    available = bool(listings or trending or movers or quotes)

    return {
        "available": available,
        "source": "CoinMarketCap",
        "as_of": datetime.now(timezone.utc).isoformat(),
        "assets": {symbol: asset for symbol, asset in all_assets.items() if not requested or symbol in requested},
        "trending": trending[:5],
        "trending_source": "cmc_trending" if results[1] else "cmc_market_activity",
        "top_gainers": top_gainers,
        "top_losers": top_losers,
        "highest_quality": highest_quality,
    }
