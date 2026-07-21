from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from time import monotonic, time

import pandas as pd

from app.config import get_settings
from app.models.schemas import PendingSetup, RiskSettings, TradeIdea
from app.services.execution_signals import dispatch_trade_ideas_to_execution
from app.services.liquidity_filter import filter_liquid_perp_markets
from app.services.market_data import get_candles_cached, get_markets_cached
from app.services.pending_setups import build_pending_setup, pending_setup_from_trade_idea
from app.services.trade_history import save_signal_reviews, save_trade_ideas
from app.strategy.market_regime import regime_score_from_dataframe
from app.strategy.decision_engine import evaluate_strategy_decision, is_actionable_v2
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
_refresh_tasks: dict[tuple[str, str], asyncio.Task] = {}
_refresh_started_at: dict[tuple[str, str], datetime] = {}
_refresh_finished_at: dict[tuple[str, str], datetime] = {}
_refresh_duration_seconds: dict[tuple[str, str], float] = {}
_scan_offsets: dict[str, int] = {}
_scan_lock = asyncio.Lock()
_background_task: asyncio.Task | None = None


@dataclass
class Candidate:
    exchange: str
    symbol: str
    fetch_symbol: str
    candles: pd.DataFrame
    volume_quality: float
    distance_score: float


@dataclass
class ScanFetchStats:
    successful_candle_fetches: int = 0
    failed_candle_fetches: int = 0
    failed_symbols: list[str] | None = None

    def __post_init__(self) -> None:
        if self.failed_symbols is None:
            self.failed_symbols = []

    def record_success(self) -> None:
        self.successful_candle_fetches += 1

    def record_failure(self, exchange: str, symbol: str, timeframe: str, error: Exception) -> None:
        self.failed_candle_fetches += 1
        label = f"{exchange}:{symbol}:{timeframe}"
        if self.failed_symbols is not None and label not in self.failed_symbols:
            self.failed_symbols.append(label)
        logger.warning(
            "Scanner candle fetch failed exchange=%s symbol=%s timeframe=%s error=%s",
            exchange,
            symbol,
            timeframe,
            error,
        )


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
                "exchange_symbol": market.get("exchange_symbol", symbol),
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


async def _scan_candles(exchange: str, symbol: str, timeframe: str, limit: int, stats: ScanFetchStats) -> pd.DataFrame:
    try:
        df = await get_candles_cached(exchange, symbol, timeframe, limit)
        stats.record_success()
        return df
    except Exception as exc:
        stats.record_failure(exchange, symbol, timeframe, exc)
        raise


def btc_regime_from_scores(score_4h: float | None, score_1d: float | None) -> dict:
    scores = [score for score in (score_4h, score_1d) if score is not None]
    if not scores:
        regime = "ranging"
        average = 0.0
    else:
        average = round(sum(scores) / len(scores), 1)
        if average <= -25 or any(score <= -55 for score in scores):
            regime = "bearish"
        elif average >= 25 or any(score >= 55 for score in scores):
            regime = "bullish"
        else:
            regime = "ranging"
    return {"regime": regime, "score_4h": score_4h, "score_1d": score_1d, "score": average}


async def btc_market_context(exchange: str, stats: ScanFetchStats | None = None) -> dict:
    selected_exchange = "hyperliquid" if normalize_exchange(exchange) == "all" else normalize_exchange(exchange)
    local_stats = stats or ScanFetchStats()
    score_4h: float | None = None
    score_1d: float | None = None
    try:
        btc_4h = await _scan_candles(selected_exchange, "BTCUSDT", "4h", 220, local_stats)
        score_4h = regime_score_from_dataframe(btc_4h)
    except Exception as exc:
        logger.info("BTC 4H context unavailable exchange=%s error=%s", selected_exchange, exc)
    try:
        btc_1d = await _scan_candles(selected_exchange, "BTCUSDT", "1d", 220, local_stats)
        score_1d = regime_score_from_dataframe(btc_1d)
    except Exception as exc:
        logger.info("BTC 1D context unavailable exchange=%s error=%s", selected_exchange, exc)
    context = btc_regime_from_scores(score_4h, score_1d)
    logger.info(
        "BTC market context exchange=%s regime=%s score_4h=%s score_1d=%s score=%s",
        selected_exchange,
        context["regime"],
        context["score_4h"],
        context["score_1d"],
        context["score"],
    )
    return context


async def _prefilter_market(market: dict, timeframe: str, semaphore: asyncio.Semaphore, stats: ScanFetchStats) -> Candidate | None:
    async with semaphore:
        try:
            fetch_symbol = str(market.get("exchange_symbol") or market["symbol"])
            df = await _scan_candles(market["exchange"], fetch_symbol, timeframe, PREFILTER_LIMIT, stats)
            ok, volume, distance = prefilter_dataframe(df)
            if not ok:
                return None
            return Candidate(market["exchange"], market["symbol"], fetch_symbol, df, volume, distance)
        except Exception as exc:
            logger.debug("Prefilter skipped %s %s: %s", market.get("exchange"), market.get("symbol"), exc)
            return None


async def _analyze_candidate(
    candidate: Candidate,
    timeframe: str,
    risk: RiskSettings,
    semaphore: asyncio.Semaphore,
    stats: ScanFetchStats,
) -> list[TradeIdea]:
    async with semaphore:
        try:
            df = candidate.candles
            if len(df) < 80:
                return []
            htf_dfs = []
            for htf in higher_timeframes_for(timeframe):
                try:
                    htf_dfs.append(await _scan_candles(candidate.exchange, candidate.fetch_symbol, htf, 220, stats))
                except Exception:
                    continue
            analysis = analyze_dataframe(candidate.symbol, timeframe, candidate.exchange, df, risk, htf_dfs)
            save_signal_reviews(analysis.rejected_signals)
            valid = [idea for idea in analysis.trade_ideas if (idea.setup_score or idea.confidence_score) >= MIN_SETUP_SCORE]
            for idea in valid:
                evaluate_strategy_decision(idea)
            return valid
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
        fetch_stats = ScanFetchStats()
        candidates_raw = await asyncio.gather(*[_prefilter_market(market, timeframe, semaphore, fetch_stats) for market in scan_markets])
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

        async def analyze_with_context(candidate: Candidate) -> tuple[list[TradeIdea], PendingSetup | None]:
            async with semaphore:
                try:
                    df = candidate.candles
                    if len(df) < 80:
                        return [], None
                    htf_dfs = []
                    for htf in higher_timeframes_for(timeframe):
                        try:
                            htf_dfs.append(await _scan_candles(candidate.exchange, candidate.fetch_symbol, htf, 220, fetch_stats))
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
                    valid_ideas = [idea for idea in analysis.trade_ideas if (idea.setup_score or idea.confidence_score) >= MIN_SETUP_SCORE]
                    for idea in valid_ideas:
                        evaluate_strategy_decision(idea)
                    pending_setup = None if valid_ideas else build_pending_setup(analysis, df)
                    return valid_ideas, pending_setup
                except Exception as exc:
                    logger.debug("Full scan skipped %s %s: %s", candidate.exchange, candidate.symbol, exc)
                    return [], None

        analyzed = await asyncio.gather(*[analyze_with_context(candidate) for candidate in candidates])
        ideas = [idea for group, _ in analyzed for idea in group]
        pending_setups = sorted(
            [pending for _, pending in analyzed if pending is not None],
            key=lambda pending: (pending.score_preview, pending.estimated_rr or 0),
            reverse=True,
        )[:20]
        ranked_all = sorted(
            ideas,
            key=lambda idea: (
                idea.setup_score or idea.confidence_score,
                idea.risk_reward_ratio,
                idea.rank_score,
            ),
            reverse=True,
        )
        # Disabled or shadow candidates must never crowd a validated strategy
        # out of the actionable top five. Persist bounded cohorts separately so
        # forward analytics remain useful without producing normal trade volume.
        executable_ranked = [idea for idea in ranked_all if is_actionable_v2(idea)][:5]
        shadow_ranked = [idea for idea in ranked_all if idea.strategy_decision == "SHADOW"][:5]
        wait_ranked = [idea for idea in ranked_all if idea.strategy_decision == "WAIT_FOR_RETEST"][:5]
        no_trade_ranked = [idea for idea in ranked_all if idea.strategy_decision == "NO_TRADE"][:3]
        ranked = [*executable_ranked, *shadow_ranked, *wait_ranked, *no_trade_ranked]
        save_trade_ideas(ranked)
        retest_pending = [
            pending_setup_from_trade_idea(idea)
            for idea in wait_ranked
            if idea.entry_status == "WAIT_FOR_RETEST" and idea.strategy_decision == "WAIT_FOR_RETEST"
        ]
        pending_setups = sorted(
            [*pending_setups, *retest_pending],
            key=lambda pending: (pending.score_preview, pending.estimated_rr or 0),
            reverse=True,
        )[:20]
        await dispatch_trade_ideas_to_execution(executable_ranked)
        duration = round(monotonic() - started, 2)
        result = {
            "timeframe": timeframe,
            "exchange": selected_exchange,
            "ideas": executable_ranked,
            "pending_setups": pending_setups,
            "errors": [],
            "message": None if len(executable_ranked) >= 5 else f"Only {len(executable_ranked)} validated executable setups found. Shadow and retest decisions remain non-actionable.",
            "scan_stats": {
                "markets": len(markets),
                "scan_window": len(scan_markets),
                "filtered": len(candidates),
                "analyzed": len(candidates),
                "valid_setups": len(executable_ranked),
                "shadow_setups": len(shadow_ranked),
                "no_trade_decisions": len(no_trade_ranked),
                "pending_setups": len(pending_setups),
                "successful_candle_fetches": fetch_stats.successful_candle_fetches,
                "failed_candle_fetches": fetch_stats.failed_candle_fetches,
                "failed_symbols": fetch_stats.failed_symbols or [],
                "duration_seconds": duration,
                "global_regime_score": global_score,
                "breadth_above_ma_pct": breadth_above_ma_pct,
                "by_exchange": {
                    current_exchange: {
                        "markets": len([market for market in markets if market["exchange"] == current_exchange]),
                        "scan_window": len([market for market in scan_markets if market["exchange"] == current_exchange]),
                        "valid_setups": len([idea for idea in executable_ranked if idea.exchange == current_exchange]),
                    }
                    for current_exchange in selected_exchanges(selected_exchange)
                },
            },
        }
        _scan_cache[key] = (monotonic(), result)
        logger.info(
            "Scan completed: Markets: %s Scan window: %s Filtered: %s Analyzed: %s Valid setups: %s Candle successes: %s Candle failures: %s Failed symbols: %s Time: %ss",
            len(markets),
            len(scan_markets),
            len(candidates),
            len(candidates),
            len(ranked),
            fetch_stats.successful_candle_fetches,
            fetch_stats.failed_candle_fetches,
            fetch_stats.failed_symbols or [],
            duration,
        )
        return result


async def cached_top_ideas(exchange: str, timeframe: str) -> dict:
    selected_exchange = normalize_exchange(exchange)
    normalized_timeframe = timeframe.lower()
    key = (selected_exchange, normalized_timeframe)
    cached = _scan_cache.get(key)
    cache_age = monotonic() - cached[0] if cached else None
    if cached is None or cache_age is None or cache_age > SCAN_TTL_SECONDS:
        trigger_top_ideas_refresh(selected_exchange, normalized_timeframe)
    if cached:
        return _with_refresh_metadata(key, cached[1])
    return _with_refresh_metadata(
        key,
        {
            "timeframe": timeframe,
            "exchange": selected_exchange,
            "ideas": [],
            "pending_setups": [],
            "errors": [],
            "message": "Scanner cache is warming up. Results will refresh shortly.",
            "scan_stats": {
                "markets": 0,
                "scan_window": 0,
                "filtered": 0,
                "analyzed": 0,
                "valid_setups": 0,
                "pending_setups": 0,
            },
        },
    )


def _task_running(key: tuple[str, str]) -> bool:
    task = _refresh_tasks.get(key)
    return task is not None and not task.done()


def _with_refresh_metadata(key: tuple[str, str], result: dict) -> dict:
    cached = _scan_cache.get(key)
    output = {**result}
    cache_age = round(monotonic() - cached[0], 2) if cached else None
    started = _refresh_started_at.get(key)
    finished = _refresh_finished_at.get(key)
    refresh_in_progress = _task_running(key)
    output.update(
        {
            "cache_age_seconds": cache_age,
            "last_refresh_started_at": started.isoformat() if started else None,
            "last_refresh_finished_at": finished.isoformat() if finished else None,
            "refresh_in_progress": refresh_in_progress,
            "refreshing": refresh_in_progress,
            "scan_duration_seconds": _refresh_duration_seconds.get(key),
        }
    )
    return output


def trigger_top_ideas_refresh(exchange: str, timeframe: str, *, force: bool = True) -> dict:
    selected_exchange = normalize_exchange(exchange)
    normalized_timeframe = timeframe.lower()
    key = (selected_exchange, normalized_timeframe)
    if _task_running(key):
        return _refresh_status(key, started=False)
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return _refresh_status(key, started=False)
    _refresh_started_at[key] = datetime.now(UTC).replace(microsecond=0)
    task = loop.create_task(_refresh_top_ideas_cache(selected_exchange, normalized_timeframe, force=force))
    _refresh_tasks[key] = task
    task.add_done_callback(lambda _: _refresh_tasks.pop(key, None))
    return _refresh_status(key, started=True)


def _refresh_status(key: tuple[str, str], *, started: bool) -> dict:
    cached = _scan_cache.get(key)
    return {
        "exchange": key[0],
        "timeframe": key[1],
        "started": started,
        "refresh_in_progress": _task_running(key),
        "cache_age_seconds": round(monotonic() - cached[0], 2) if cached else None,
        "last_refresh_started_at": _refresh_started_at[key].isoformat() if key in _refresh_started_at else None,
        "last_refresh_finished_at": _refresh_finished_at[key].isoformat() if key in _refresh_finished_at else None,
        "scan_duration_seconds": _refresh_duration_seconds.get(key),
    }


async def _refresh_top_ideas_cache(exchange: str, timeframe: str, *, force: bool) -> None:
    key = (normalize_exchange(exchange), timeframe.lower())
    started = monotonic()
    try:
        await run_scan(exchange=exchange, timeframe=timeframe, force=force)
    except Exception:
        logger.exception("Background top ideas refresh failed exchange=%s timeframe=%s", exchange, timeframe)
    finally:
        duration = round(monotonic() - started, 2)
        _refresh_duration_seconds[key] = duration
        _refresh_finished_at[key] = datetime.now(UTC).replace(microsecond=0)


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
            timeframe = get_settings().default_timeframe
            for exchange in ("all", "hyperliquid"):
                trigger_top_ideas_refresh(exchange, timeframe, force=True)
        except Exception:
            logger.exception("Background scan failed")
        await asyncio.sleep(SCAN_INTERVAL_SECONDS)
