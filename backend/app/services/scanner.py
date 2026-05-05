from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from time import monotonic

import pandas as pd

from app.config import DEFAULT_SCAN_LIST, get_settings
from app.models.schemas import RiskSettings, TradeIdea
from app.services.market_data import get_candles_cached, get_markets_cached
from app.services.trade_history import save_trade_ideas
from app.strategy.support_resistance import average_true_range
from app.strategy.trade_ideas import MIN_SETUP_SCORE, analyze_dataframe

logger = logging.getLogger(__name__)

SCAN_TTL_SECONDS = 120
SCAN_INTERVAL_SECONDS = 120
MAX_CONCURRENT_FETCHES = 8
PREFILTER_LIMIT = 260
FULL_LIMIT = 260
_scan_cache: dict[tuple[str, str], tuple[float, dict]] = {}
_scan_lock = asyncio.Lock()
_background_task: asyncio.Task | None = None


@dataclass
class Candidate:
    exchange: str
    symbol: str
    candles: pd.DataFrame
    volume_quality: float
    distance_score: float


def higher_timeframes_for(timeframe: str) -> list[str]:
    normalized = timeframe.lower()
    if normalized in {"30m", "1h"}:
        return ["4h", "1d"]
    if normalized in {"2h", "4h", "6h", "8h", "12h"}:
        return ["1d"]
    return []


def _risk(timeframe: str) -> RiskSettings:
    settings = get_settings()
    return RiskSettings(
        account_size=settings.default_account_size,
        risk_per_trade_pct=settings.default_risk_per_trade,
        min_rr=settings.default_min_rr,
        max_open_trades=settings.default_max_open_trades,
        preferred_timeframe=timeframe,
    )


async def discover_scan_markets(exchange: str) -> list[dict]:
    exchanges = ["binance", "hyperliquid"] if exchange.lower() == "all" else [exchange.lower()]
    output: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for name in exchanges:
        try:
            markets = await get_markets_cached(name)
        except Exception as exc:
            logger.warning("Could not discover markets for %s: %s", name, exc)
            markets = []
        if name == "binance":
            preferred = {symbol.upper() for symbol in DEFAULT_SCAN_LIST}
            markets = [market for market in markets if market.get("symbol") in preferred]
            if not markets:
                markets = [
                    {
                        "symbol": symbol,
                        "exchange": name,
                        "volume": None,
                        "active": True,
                    }
                    for symbol in DEFAULT_SCAN_LIST
                ]
        for market in markets:
            symbol = str(market.get("symbol", "")).upper()
            if not symbol or not market.get("active", True):
                continue
            key = (name, symbol)
            if key in seen:
                continue
            seen.add(key)
            output.append({"exchange": name, "symbol": symbol, "volume": market.get("volume"), "active": True})
    return output


def prefilter_dataframe(df: pd.DataFrame) -> tuple[bool, float, float]:
    if len(df) < 70:
        return False, 0, 0
    tail = df.tail(80)
    volume = float(tail["volume"].tail(24).mean())
    if volume <= 0:
        return False, 0, 0
    close = float(tail["close"].iloc[-1])
    high = float(tail["high"].max())
    low = float(tail["low"].min())
    width = high - low
    if close <= 0 or width <= close * 0.006:
        return False, volume, 0
    atr = average_true_range(tail)
    if atr <= 0 or atr / close < 0.002:
        return False, volume, 0
    position = (close - low) / width
    distance_from_mid = abs(position - 0.5)
    near_edge = position <= 0.32 or position >= 0.68
    recent_range = tail["close"].tail(12).max() - tail["close"].tail(12).min()
    choppy = recent_range < atr * 1.2 and 0.32 < position < 0.68
    return bool(near_edge and not choppy), volume, distance_from_mid


async def _prefilter_market(market: dict, timeframe: str, semaphore: asyncio.Semaphore) -> Candidate | None:
    async with semaphore:
        try:
            df = await get_candles_cached(market["exchange"], market["symbol"], timeframe, PREFILTER_LIMIT)
            ok, volume, distance = prefilter_dataframe(df)
            if not ok:
                return None
            return Candidate(market["exchange"], market["symbol"], df, volume, distance)
        except Exception as exc:
            logger.debug("Prefilter skipped %s %s: %s", market.get("exchange"), market.get("symbol"), exc)
            return None


async def _analyze_candidate(candidate: Candidate, timeframe: str, risk: RiskSettings, semaphore: asyncio.Semaphore) -> list[TradeIdea]:
    async with semaphore:
        try:
            df = candidate.candles
            if len(df) < 80:
                return []
            htf_dfs = []
            for htf in higher_timeframes_for(timeframe):
                try:
                    htf_dfs.append(await get_candles_cached(candidate.exchange, candidate.symbol, htf, 220))
                except Exception:
                    continue
            analysis = analyze_dataframe(candidate.symbol, timeframe, candidate.exchange, df, risk, htf_dfs)
            return [idea for idea in analysis.trade_ideas if (idea.setup_score or idea.confidence_score) >= MIN_SETUP_SCORE]
        except Exception as exc:
            logger.debug("Full scan skipped %s %s: %s", candidate.exchange, candidate.symbol, exc)
            return []


async def run_scan(exchange: str = "all", timeframe: str = "4h", *, force: bool = False) -> dict:
    key = (exchange.lower(), timeframe.lower())
    now = monotonic()
    cached = _scan_cache.get(key)
    if cached and not force and now - cached[0] < SCAN_TTL_SECONDS:
        return cached[1]

    async with _scan_lock:
        cached = _scan_cache.get(key)
        now = monotonic()
        if cached and not force and now - cached[0] < SCAN_TTL_SECONDS:
            return cached[1]

        started = monotonic()
        markets = await discover_scan_markets(exchange)
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_FETCHES)
        candidates_raw = await asyncio.gather(*[_prefilter_market(market, timeframe, semaphore) for market in markets])
        candidates = [candidate for candidate in candidates_raw if candidate is not None]
        candidates = sorted(candidates, key=lambda item: (item.distance_score, item.volume_quality), reverse=True)[:80]
        risk = _risk(timeframe)
        analyzed = await asyncio.gather(*[_analyze_candidate(candidate, timeframe, risk, semaphore) for candidate in candidates])
        ideas = [idea for group in analyzed for idea in group]
        ranked = sorted(
            ideas,
            key=lambda idea: (
                idea.setup_score or idea.confidence_score,
                idea.risk_reward_ratio,
                idea.rank_score,
            ),
            reverse=True,
        )[:5]
        save_trade_ideas(ranked)
        duration = round(monotonic() - started, 2)
        result = {
            "timeframe": timeframe,
            "exchange": exchange,
            "ideas": ranked,
            "errors": [],
            "message": None if len(ranked) >= 5 else f"Only {len(ranked)} valid setups found. Other coins are currently no-trade.",
            "scan_stats": {
                "markets": len(markets),
                "filtered": len(candidates),
                "analyzed": len(candidates),
                "valid_setups": len(ranked),
                "duration_seconds": duration,
            },
        }
        _scan_cache[key] = (monotonic(), result)
        logger.info(
            "Scan completed: Markets: %s Filtered: %s Analyzed: %s Valid setups: %s Time: %ss",
            len(markets),
            len(candidates),
            len(candidates),
            len(ranked),
            duration,
        )
        return result


async def cached_top_ideas(exchange: str, timeframe: str) -> dict:
    return await run_scan(exchange=exchange, timeframe=timeframe, force=False)


def start_background_scanner() -> None:
    global _background_task
    if _background_task is not None and not _background_task.done():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    _background_task = loop.create_task(_scan_loop())


async def _scan_loop() -> None:
    await asyncio.sleep(5)
    while True:
        try:
            await run_scan(exchange="all", timeframe=get_settings().default_timeframe, force=True)
        except Exception:
            logger.exception("Background scan failed")
        await asyncio.sleep(SCAN_INTERVAL_SECONDS)
