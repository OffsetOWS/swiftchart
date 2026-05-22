from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from time import monotonic, time

import pandas as pd

from app.config import get_settings
from app.models.schemas import RiskSettings, TradeIdea
from app.services.execution_signals import dispatch_trade_ideas_to_execution
from app.services.liquidity_filter import filter_liquid_perp_markets
from app.services.market_data import get_candles_cached, get_markets_cached
from app.services.trade_history import save_signal_reviews, save_trade_ideas
from app.strategy.market_regime import regime_score_from_dataframe
from app.strategy.support_resistance import average_true_range
from app.strategy.trade_ideas import MIN_SETUP_SCORE, analyze_dataframe

logger = logging.getLogger(__name__)

SCAN_TTL_SECONDS = 120
SCAN_INTERVAL_SECONDS = 120
MAX_CONCURRENT_FETCHES = 8
MAX_MARKETS_PER_SCAN = 45
PREFILTER_LIMIT = 260
FULL_LIMIT = 260
_scan_cache: dict[tuple[str, str], tuple[float, dict]] = {}
_scan_offsets: dict[str, int] = {}
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


def normalize_exchange(exchange: str | None) -> str:
    requested = (exchange or get_settings().default_exchange).lower()
    return requested


def selected_exchanges(exchange: str | None) -> list[str]:
    normalized = normalize_exchange(exchange)
    if normalized != "all":
        return [normalized]
    exchanges = ["hyperliquid"]
    if get_settings().variational_enabled:
        exchanges.append("variational")
    return exchanges


def max_markets_for_timeframe(timeframe: str) -> int:
    normalized = timeframe.lower()
    if normalized in {"30m", "1h"}:
        return 24
    if normalized == "2h":
        return 32
    return MAX_MARKETS_PER_SCAN


def max_candidates_for_timeframe(timeframe: str) -> int:
    normalized = timeframe.lower()
    if normalized in {"30m", "1h"}:
        return 32
    if normalized == "2h":
        return 48
    return 80


async def discover_scan_markets(exchange: str) -> list[dict]:
    selected_exchange = normalize_exchange(exchange)
    output: list[dict] = []
    seen: set[tuple[str, str]] = set()
    try:
        markets = await get_markets_cached(selected_exchange)
    except Exception as exc:
        logger.warning("Could not discover markets for %s: %s", selected_exchange, exc)
        markets = []
    for market in markets:
        symbol = str(market.get("symbol", "")).upper()
        if not symbol or not market.get("active", True):
            continue
        key = (selected_exchange, symbol)
        if key in seen:
            continue
        seen.add(key)
        output.append(
            {
                "exchange": selected_exchange,
                "symbol": symbol,
                "volume": market.get("volume"),
                "perpVolume24h": market.get("perpVolume24h"),
                "active": True,
            }
        )
    return filter_liquid_perp_markets(output)


async def discover_all_scan_markets(exchange: str) -> list[dict]:
    markets = []
    for selected_exchange in selected_exchanges(exchange):
        discovered = await discover_scan_markets(selected_exchange)
        logger.info("%s markets found: %s", selected_exchange.title(), len(discovered))
        markets.extend(discovered)
    return markets


def scan_window(exchange: str, markets: list[dict], limit: int = MAX_MARKETS_PER_SCAN, *, timeframe: str = "4h") -> list[dict]:
    if len(markets) <= limit:
        return markets
    key = f"{exchange.lower()}:{timeframe.lower()}"
    slot = int(time() // SCAN_INTERVAL_SECONDS)
    timeframe_seed = sum(ord(char) for char in timeframe.lower())
    default_start = ((slot + timeframe_seed) * limit) % len(markets)
    start = _scan_offsets.get(key, default_start) % len(markets)
    end = start + limit
    selected = markets[start:end]
    if len(selected) < limit:
        selected.extend(markets[: limit - len(selected)])
    _scan_offsets[key] = (start + limit) % len(markets)
    return selected


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
            save_signal_reviews(analysis.rejected_signals)
            return [idea for idea in analysis.trade_ideas if (idea.setup_score or idea.confidence_score) >= MIN_SETUP_SCORE]
        except Exception as exc:
            logger.debug("Full scan skipped %s %s: %s", candidate.exchange, candidate.symbol, exc)
            return []


async def run_scan(exchange: str = "hyperliquid", timeframe: str = "4h", *, force: bool = False) -> dict:
    selected_exchange = normalize_exchange(exchange)
    key = (selected_exchange, timeframe.lower())
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
        market_limit = max_markets_for_timeframe(timeframe)
        candidate_limit = max_candidates_for_timeframe(timeframe)
        markets = await discover_all_scan_markets(selected_exchange)
        scan_markets = []
        for current_exchange in selected_exchanges(selected_exchange):
            exchange_markets = [market for market in markets if market["exchange"] == current_exchange]
            selected_window = scan_window(current_exchange, exchange_markets, market_limit, timeframe=timeframe)
            logger.info("%s markets selected for scan: %s", current_exchange.title(), len(selected_window))
            scan_markets.extend(selected_window)
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_FETCHES)
        candidates_raw = await asyncio.gather(*[_prefilter_market(market, timeframe, semaphore) for market in scan_markets])
        candidates = [candidate for candidate in candidates_raw if candidate is not None]
        candidates = sorted(candidates, key=lambda item: (item.distance_score, item.volume_quality), reverse=True)[:candidate_limit]
        breadth_values = []
        global_scores = []
        for candidate in candidates:
            close = candidate.candles["close"].astype(float)
            if len(close) >= 80:
                ema = close.ewm(span=200 if len(close) >= 200 else 100, adjust=False).mean()
                breadth_values.append(float(close.iloc[-1]) > float(ema.iloc[-1]))
            if candidate.symbol.upper() in {"BTCUSDT", "ETHUSDT"}:
                global_scores.append(regime_score_from_dataframe(candidate.candles))
        breadth_above_ma_pct = round(sum(1 for value in breadth_values if value) / len(breadth_values) * 100, 1) if breadth_values else None
        global_score = round(sum(global_scores) / len(global_scores), 1) if global_scores else None
        risk = _risk(timeframe)

        async def analyze_with_context(candidate: Candidate) -> list[TradeIdea]:
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
                    analysis = analyze_dataframe(
                        candidate.symbol,
                        timeframe,
                        candidate.exchange,
                        df,
                        risk,
                        htf_dfs,
                        global_regime_score=global_score,
                        breadth_above_ma_pct=breadth_above_ma_pct,
                    )
                    save_signal_reviews(analysis.rejected_signals)
                    return [idea for idea in analysis.trade_ideas if (idea.setup_score or idea.confidence_score) >= MIN_SETUP_SCORE]
                except Exception as exc:
                    logger.debug("Full scan skipped %s %s: %s", candidate.exchange, candidate.symbol, exc)
                    return []

        analyzed = await asyncio.gather(*[analyze_with_context(candidate) for candidate in candidates])
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
        await dispatch_trade_ideas_to_execution(ranked)
        duration = round(monotonic() - started, 2)
        result = {
            "timeframe": timeframe,
            "exchange": selected_exchange,
            "ideas": ranked,
            "errors": [],
            "message": None if len(ranked) >= 5 else f"Only {len(ranked)} valid setups found. Other coins are currently no-trade.",
            "scan_stats": {
                "markets": len(markets),
                "scan_window": len(scan_markets),
                "filtered": len(candidates),
                "analyzed": len(candidates),
                "valid_setups": len(ranked),
                "duration_seconds": duration,
                "global_regime_score": global_score,
                "breadth_above_ma_pct": breadth_above_ma_pct,
                "by_exchange": {
                    current_exchange: {
                        "markets": len([market for market in markets if market["exchange"] == current_exchange]),
                        "scan_window": len([market for market in scan_markets if market["exchange"] == current_exchange]),
                        "valid_setups": len([idea for idea in ranked if idea.exchange == current_exchange]),
                    }
                    for current_exchange in selected_exchanges(selected_exchange)
                },
            },
        }
        _scan_cache[key] = (monotonic(), result)
        logger.info(
            "Scan completed: Markets: %s Scan window: %s Filtered: %s Analyzed: %s Valid setups: %s Time: %ss",
            len(markets),
            len(scan_markets),
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
