from __future__ import annotations

import asyncio
import json
import logging
import os
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic, time

import pandas as pd

from app.config import get_settings
from app.models.schemas import AnalysisResponse, RiskSettings, TradeIdea, Zone
from app.services.alert_dedupe import setup_fingerprint, should_skip_alert
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
_last_health: dict = {
    "scanner_running": False,
    "last_scan_started_at": None,
    "last_scan_finished_at": None,
    "exchange": None,
    "timeframe": None,
    "markets_fetched": 0,
    "markets_analyzed": 0,
    "candidates_prefilter_passed": 0,
    "setups_created": 0,
    "setups_after_qc": 0,
    "website_visible_count": 0,
    "telegram_eligible_count": 0,
    "candle_fetch_errors": 0,
    "successful_candle_fetches": 0,
    "failed_candle_symbols": [],
    "setup_block_reasons": [],
    "prefilter_passed_markets": [],
    "top_rejection_reasons": [],
    "last_error": None,
}


@dataclass
class Candidate:
    exchange: str
    symbol: str
    candles: pd.DataFrame
    volume_quality: float
    distance_score: float


@dataclass
class PrefilterResult:
    market: dict
    candidate: Candidate | None
    reason: str | None
    candles: int = 0
    recent_volume: float = 0.0
    atr_ratio: float | None = None
    range_width_pct: float | None = None
    range_position: float | None = None


@dataclass
class CandleFetchStats:
    successful: int = 0
    errors: int = 0
    failed_symbols: list[dict] | None = None

    def __post_init__(self) -> None:
        if self.failed_symbols is None:
            self.failed_symbols = []


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def debug_scanner_enabled() -> bool:
    return os.getenv("DEBUG_SCANNER", "").lower() in {"1", "true", "yes", "on"}


async def fetch_candles_resilient(
    exchange: str,
    symbol: str,
    timeframe: str,
    limit: int,
    stats: CandleFetchStats,
) -> pd.DataFrame:
    attempts = 3
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            df = await get_candles_cached(exchange, symbol, timeframe, limit)
            stats.successful += 1
            return df
        except Exception as exc:
            last_error = exc
            if attempt < attempts - 1:
                await asyncio.sleep(0.25 * (attempt + 1))
    stats.errors += 1
    failure = {
        "exchange": exchange,
        "symbol": symbol,
        "timeframe": timeframe,
        "error": str(last_error),
    }
    stats.failed_symbols.append(failure)
    logger.warning(
        "Candle fetch failed exchange=%s symbol=%s timeframe=%s limit=%s error=%s",
        exchange,
        symbol,
        timeframe,
        limit,
        last_error,
    )
    raise last_error  # type: ignore[misc]


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
    return output


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


def prefilter_diagnostics(df: pd.DataFrame) -> tuple[bool, float, float, str | None, dict]:
    details = {
        "candles": len(df),
        "recent_volume": 0.0,
        "atr_ratio": None,
        "range_width_pct": None,
        "range_position": None,
    }
    if len(df) < 70:
        return False, 0, 0, "insufficient candles", details
    tail = df.tail(80)
    volume = float(tail["volume"].tail(24).mean())
    details["recent_volume"] = round(volume, 8)
    if volume <= 0:
        return False, 0, 0, "zero recent volume", details
    close = float(tail["close"].iloc[-1])
    high = float(tail["high"].max())
    low = float(tail["low"].min())
    width = high - low
    details["range_width_pct"] = round(width / close, 6) if close > 0 else None
    if close <= 0 or width <= close * 0.006:
        return False, volume, 0, "recent range too tiny", details
    atr = average_true_range(tail)
    details["atr_ratio"] = round(atr / close, 6) if close > 0 else None
    if atr <= 0 or atr / close < 0.002:
        return False, volume, 0, "ATR/close below threshold", details
    position = (close - low) / width
    details["range_position"] = round(position, 4)
    distance_from_mid = abs(position - 0.5)
    near_edge = position <= 0.32 or position >= 0.68
    recent_range = tail["close"].tail(12).max() - tail["close"].tail(12).min()
    choppy = recent_range < atr * 1.2 and 0.32 < position < 0.68
    if not near_edge:
        return False, volume, distance_from_mid, "price not near range edge", details
    if choppy:
        return False, volume, distance_from_mid, "middle chop", details
    return True, volume, distance_from_mid, None, details


def prefilter_dataframe(df: pd.DataFrame) -> tuple[bool, float, float]:
    ok, volume, distance, _, _ = prefilter_diagnostics(df)
    return ok, volume, distance


async def _prefilter_market(market: dict, timeframe: str, semaphore: asyncio.Semaphore, fetch_stats: CandleFetchStats) -> PrefilterResult:
    async with semaphore:
        try:
            df = await fetch_candles_resilient(market["exchange"], market["symbol"], timeframe, PREFILTER_LIMIT, fetch_stats)
            ok, volume, distance, reason, details = prefilter_diagnostics(df)
            if not ok:
                return PrefilterResult(market=market, candidate=None, reason=reason, **details)
            candidate = Candidate(market["exchange"], market["symbol"], df, volume, distance)
            return PrefilterResult(market=market, candidate=candidate, reason=None, **details)
        except Exception as exc:
            logger.debug("Prefilter skipped %s %s: %s", market.get("exchange"), market.get("symbol"), exc)
            return PrefilterResult(market=market, candidate=None, reason=f"candle fetch error: {exc}")


async def _analyze_candidate(candidate: Candidate, timeframe: str, risk: RiskSettings, semaphore: asyncio.Semaphore, fetch_stats: CandleFetchStats) -> list[TradeIdea]:
    async with semaphore:
        try:
            df = candidate.candles
            if len(df) < 80:
                return []
            htf_dfs = []
            for htf in higher_timeframes_for(timeframe):
                try:
                    htf_dfs.append(await fetch_candles_resilient(candidate.exchange, candidate.symbol, htf, 220, fetch_stats))
                except Exception:
                    continue
            analysis = analyze_dataframe(candidate.symbol, timeframe, candidate.exchange, df, risk, htf_dfs)
            save_signal_reviews(analysis.rejected_signals)
            return [idea for idea in analysis.trade_ideas if (idea.setup_score or idea.confidence_score) >= MIN_SETUP_SCORE]
        except Exception as exc:
            logger.debug("Full scan skipped %s %s: %s", candidate.exchange, candidate.symbol, exc)
            return []


def _idea_score(idea: TradeIdea) -> float:
    return float(idea.setup_score or idea.confidence_score or 0)


def _trigger_type(idea: TradeIdea) -> str:
    if idea.market_regime in {"BREAKOUT", "BREAKDOWN"}:
        return idea.market_regime.lower()
    if idea.market_regime == "RANGE_BOUND":
        return "range edge"
    if idea.is_regime_transition:
        return "transition trigger"
    if idea.trend_alignment == "with-trend":
        return "trend continuation"
    if idea.trend_alignment == "counter-trend":
        return "counter-trend reversal"
    return "setup trigger"


def _zone_mid(zone: Zone | None) -> float | None:
    if zone is None:
        return None
    return round((float(zone.lower) + float(zone.upper)) / 2, 8)


def _nearest_zones(price: float, supports: list[Zone], resistances: list[Zone]) -> tuple[Zone | None, Zone | None]:
    support = min(supports, key=lambda zone: abs(price - _zone_mid(zone)), default=None)
    resistance = min(resistances, key=lambda zone: abs(price - _zone_mid(zone)), default=None)
    return support, resistance


def _position_between_zones(price: float, support: Zone | None, resistance: Zone | None) -> float | None:
    if support is None or resistance is None:
        return None
    width = float(resistance.lower) - float(support.upper)
    if width <= 0:
        return None
    return round(max(0.0, min(1.0, (price - float(support.upper)) / width)), 4)


def _trigger_detected(analysis: AnalysisResponse) -> bool:
    if analysis.trade_ideas:
        return True
    if any(sweep.confirmation_status == "confirmed" for sweep in analysis.liquidity_sweeps):
        return True
    for review in analysis.rejected_signals:
        reason = (review.reason or "").lower()
        if "no rejection" in reason or "trigger not ready" in reason or "not at resistance/retest edge" in reason:
            continue
        if review.base_score is not None:
            return True
    return False


def _review_raw_score(analysis: AnalysisResponse) -> float | None:
    scores = [float(review.base_score) for review in analysis.rejected_signals if review.base_score is not None]
    scores.extend(_idea_score(idea) for idea in analysis.trade_ideas)
    return round(max(scores), 1) if scores else None


def _review_rr(analysis: AnalysisResponse) -> float | None:
    values = [float(idea.risk_reward_ratio) for idea in analysis.trade_ideas if idea.risk_reward_ratio is not None]
    if values:
        return round(max(values), 2)
    for review in analysis.rejected_signals:
        reason = review.reason or ""
        if "risk/reward" not in reason:
            continue
        parts = reason.split("risk/reward", 1)[-1].strip().split()
        if not parts:
            continue
        try:
            return round(float(parts[0]), 2)
        except ValueError:
            continue
    return None


def _setup_block_reason(analysis: AnalysisResponse) -> str:
    review_reasons = " ".join(review.reason or "" for review in analysis.rejected_signals).lower()
    no_trade = (analysis.no_trade_reason or analysis.warning or "").lower()
    decision = analysis.market_regime_data.trade_decision
    if "risk/reward" in review_reasons:
        return "R:R below 2.0"
    if "exhaustion" in review_reasons or "too mature" in review_reasons or "exhausted" in review_reasons:
        return "exhausted after QC"
    if "setup score is below 65" in review_reasons or "setup score is below 65" in no_trade:
        return "score below 65"
    if "unconfirmed sweep" in no_trade or "unconfirmed sweep" in review_reasons:
        return "unconfirmed sweep"
    if "not enough clean support/resistance" in no_trade:
        return "no valid support/resistance zone"
    if "no rejection" in review_reasons or "trigger not ready" in review_reasons or "not at resistance/retest edge" in review_reasons:
        return "no confirmed trigger"
    if decision == "WAIT":
        return "regime is WAIT"
    if decision == "NO_TRADE":
        return "regime is NO_TRADE"
    if "range is too compressed" in no_trade:
        return "no valid support/resistance zone"
    if "risk/reward is not good enough" in no_trade:
        return "score below 65 or R:R below 2.0"
    return "no setup created"


def _near_miss_diagnostic(analysis: AnalysisResponse) -> dict:
    price = float(analysis.current_price)
    support, resistance = _nearest_zones(price, analysis.support_zones, analysis.resistance_zones)
    return {
        "symbol": analysis.symbol,
        "regime": analysis.market_regime_data.regime_type,
        "decision": analysis.market_regime_data.trade_decision,
        "position": _position_between_zones(price, support, resistance),
        "nearest_support": _zone_mid(support),
        "nearest_resistance": _zone_mid(resistance),
        "trigger_detected": _trigger_detected(analysis),
        "raw_score": _review_raw_score(analysis),
        "rr": _review_rr(analysis),
        "block_reason": _setup_block_reason(analysis),
    }


def _bot_state_path() -> Path:
    return Path(os.getenv("BOT_STATE_PATH", ".swiftchart_bot_state.json"))


def _load_bot_state() -> dict:
    path = _bot_state_path()
    if not path.exists():
        return {"subscribers": [], "sent_alerts": []}
    try:
        data = json.loads(path.read_text())
        return {
            "subscribers": data.get("subscribers", []),
            "sent_alerts": data.get("sent_alerts", []),
        }
    except (OSError, json.JSONDecodeError):
        return {"subscribers": [], "sent_alerts": []}


def _telegram_subscribers() -> set[int]:
    data = _load_bot_state()
    subscribers = {int(chat_id) for chat_id in data.get("subscribers", [])}
    raw = os.getenv("TELEGRAM_ALERT_CHAT_IDS", "")
    for item in raw.split(","):
        item = item.strip()
        if item:
            subscribers.add(int(item))
    return subscribers


def _legacy_alert_sent(alert_key: str) -> bool:
    data = _load_bot_state()
    return alert_key in set(data.get("sent_alerts", []))


def _telegram_diagnostics(ideas: list[TradeIdea]) -> tuple[int, dict[str, int]]:
    reasons: Counter[str] = Counter()
    min_score = float(os.getenv("ALERT_MIN_SCORE", "75"))
    token_missing = not bool(os.getenv("TELEGRAM_BOT_TOKEN"))
    try:
        subscribers = _telegram_subscribers()
    except Exception as exc:
        subscribers = set()
        reasons[f"telegram subscriber state error: {exc}"] += 1

    if token_missing:
        reasons["missing bot token"] += 1
    if not subscribers:
        reasons["missing chat id"] += 1

    eligible = 0
    for idea in ideas:
        score = _idea_score(idea)
        if score < min_score:
            reasons["score below ALERT_MIN_SCORE"] += 1
            continue
        if idea.entry_status != "READY":
            reasons["entry_status not READY"] += 1
            continue
        try:
            key = setup_fingerprint(idea)
            if _legacy_alert_sent(key) or should_skip_alert(idea, namespace="telegram"):
                reasons["duplicate alert"] += 1
                continue
        except Exception as exc:
            reasons[f"telegram duplicate check error: {exc}"] += 1
            continue
        if token_missing or not subscribers:
            continue
        eligible += 1
    return eligible, dict(reasons)


def scanner_health() -> dict:
    return {
        **_last_health,
        "scanner_running": _background_task is not None and not _background_task.done(),
    }


async def run_scan(exchange: str = "hyperliquid", timeframe: str = "4h", *, force: bool = False) -> dict:
    global _last_health
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
        started_at = _now_iso()
        _last_health = {
            **_last_health,
            "scanner_running": _background_task is not None and not _background_task.done(),
            "last_scan_started_at": started_at,
            "exchange": selected_exchange,
            "timeframe": timeframe,
            "last_error": None,
        }
        market_limit = max_markets_for_timeframe(timeframe)
        candidate_limit = max_candidates_for_timeframe(timeframe)
        logger.info(
            "Scan started exchange=%s timeframe=%s force=%s market_limit=%s candidate_limit=%s",
            selected_exchange,
            timeframe,
            force,
            market_limit,
            candidate_limit,
        )
        rejection_reasons: Counter[str] = Counter()
        setup_block_reasons: Counter[str] = Counter()
        market_debug: list[dict] = []
        near_misses: list[dict] = []
        fetch_stats = CandleFetchStats()
        markets = await discover_all_scan_markets(selected_exchange)
        if not markets:
            rejection_reasons["market fetch returned zero markets"] += 1
        scan_markets = []
        for current_exchange in selected_exchanges(selected_exchange):
            exchange_markets = [market for market in markets if market["exchange"] == current_exchange]
            selected_window = scan_window(current_exchange, exchange_markets, market_limit, timeframe=timeframe)
            logger.info("%s markets selected for scan: %s", current_exchange.title(), len(selected_window))
            scan_markets.extend(selected_window)
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_FETCHES)
        prefilter_results = await asyncio.gather(*[_prefilter_market(market, timeframe, semaphore, fetch_stats) for market in scan_markets])
        for result_item in prefilter_results:
            if result_item.reason:
                rejection_reasons[result_item.reason] += 1
                market_debug.append(
                    {
                        "exchange": result_item.market.get("exchange"),
                        "symbol": result_item.market.get("symbol"),
                        "stage": "prefilter",
                        "reason": result_item.reason,
                        "candles": result_item.candles,
                        "recent_volume": result_item.recent_volume,
                        "atr_ratio": result_item.atr_ratio,
                        "range_width_pct": result_item.range_width_pct,
                        "range_position": result_item.range_position,
                    }
                )
        candidates = [result_item.candidate for result_item in prefilter_results if result_item.candidate is not None]
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
        setup_attempts = 0

        async def analyze_with_context(candidate: Candidate) -> list[TradeIdea]:
            nonlocal setup_attempts
            async with semaphore:
                try:
                    df = candidate.candles
                    if len(df) < 80:
                        return []
                    htf_dfs = []
                    for htf in higher_timeframes_for(timeframe):
                        try:
                            htf_dfs.append(await fetch_candles_resilient(candidate.exchange, candidate.symbol, htf, 220, fetch_stats))
                        except Exception as exc:
                            logger.info(
                                "Higher timeframe candles skipped exchange=%s symbol=%s timeframe=%s htf=%s error=%s",
                                candidate.exchange,
                                candidate.symbol,
                                timeframe,
                                htf,
                                exc,
                            )
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
                    setup_attempts += len(analysis.trade_ideas) + len([review for review in analysis.rejected_signals if review.base_score is not None])
                    regime = analysis.market_regime_data
                    diagnostic = _near_miss_diagnostic(analysis)
                    if analysis.trade_ideas:
                        for idea in analysis.trade_ideas:
                            logger.info(
                                (
                                    "Scanner setup result exchange=%s symbol=%s timeframe=%s regime=%s decision=%s "
                                    "confidence=%s setup_created=true direction=%s trigger=%s score_after_qc=%s "
                                    "entry_status=%s rejection_reason=%s"
                                ),
                                candidate.exchange,
                                candidate.symbol,
                                timeframe,
                                regime.regime_type,
                                regime.trade_decision,
                                regime.confidence_score,
                                idea.direction,
                                _trigger_type(idea),
                                idea.setup_score or idea.confidence_score,
                                idea.entry_status,
                                None,
                            )
                    else:
                        reason = analysis.no_trade_reason or "no setup created"
                        block_reason = diagnostic["block_reason"]
                        setup_block_reasons[block_reason] += 1
                        near_misses.append(diagnostic)
                        rejection_reasons[reason] += 1
                        market_debug.append(
                            {
                                "exchange": candidate.exchange,
                                "symbol": candidate.symbol,
                                "stage": "setup_generation",
                                "reason": reason,
                                "regime": regime.regime_type,
                                "decision": regime.trade_decision,
                                "confidence": regime.confidence_score,
                                "block_reason": block_reason,
                            }
                        )
                        logger.info(
                            (
                                "Scanner setup result exchange=%s symbol=%s timeframe=%s regime=%s decision=%s "
                                "confidence=%s setup_created=false rejection_reason=%s"
                            ),
                            candidate.exchange,
                            candidate.symbol,
                            timeframe,
                            regime.regime_type,
                            regime.trade_decision,
                            regime.confidence_score,
                            reason,
                        )
                    for review in analysis.rejected_signals:
                        reason = review.reason or "rejected by setup/QC"
                        block_reason = _setup_block_reason(analysis)
                        rejection_reasons[reason] += 1
                        market_debug.append(
                            {
                                "exchange": candidate.exchange,
                                "symbol": candidate.symbol,
                                "stage": "setup_qc",
                                "reason": reason,
                                "regime": review.regime_label,
                                "direction": review.direction,
                                "score_before_qc": review.base_score,
                                "score_after_qc": review.adjusted_score,
                                "block_reason": block_reason,
                            }
                        )
                        logger.info(
                            (
                                "Scanner QC rejection exchange=%s symbol=%s timeframe=%s direction=%s "
                                "score_before_qc=%s score_after_qc=%s rejection_reason=%s"
                            ),
                            candidate.exchange,
                            candidate.symbol,
                            timeframe,
                            review.direction,
                            review.base_score,
                            review.adjusted_score,
                            reason,
                        )
                    return [idea for idea in analysis.trade_ideas if (idea.setup_score or idea.confidence_score) >= MIN_SETUP_SCORE]
                except Exception as exc:
                    logger.debug("Full scan skipped %s %s: %s", candidate.exchange, candidate.symbol, exc)
                    rejection_reasons[f"analysis error: {exc}"] += 1
                    market_debug.append(
                        {
                            "exchange": candidate.exchange,
                            "symbol": candidate.symbol,
                            "stage": "analysis",
                            "reason": f"analysis error: {exc}",
                        }
                    )
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
        telegram_eligible_count, telegram_skip_reasons = _telegram_diagnostics(ranked)
        rejection_reasons.update(telegram_skip_reasons)
        duration = round(monotonic() - started, 2)
        setup_block_reason_list = [
            {"reason": reason, "count": count}
            for reason, count in setup_block_reasons.most_common(12)
        ]
        top_near_misses = sorted(
            near_misses,
            key=lambda item: (
                float(item["raw_score"] or 0),
                float(item["rr"] or 0),
            ),
            reverse=True,
        )[:10]
        top_rejection_reasons = [
            {"reason": reason, "count": count}
            for reason, count in rejection_reasons.most_common(12)
        ]
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
                "telegram_eligible": telegram_eligible_count,
                "telegram_skip_reasons": telegram_skip_reasons,
                "top_rejection_reasons": top_rejection_reasons,
                "setup_block_reasons": setup_block_reason_list,
                "prefilter_passed_markets": top_near_misses,
                "candle_fetch_errors": fetch_stats.errors,
                "successful_candle_fetches": fetch_stats.successful,
                "failed_candle_symbols": fetch_stats.failed_symbols[:20],
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
        _last_health = {
            "scanner_running": _background_task is not None and not _background_task.done(),
            "last_scan_started_at": started_at,
            "last_scan_finished_at": _now_iso(),
            "exchange": selected_exchange,
            "timeframe": timeframe,
            "markets_fetched": len(markets),
            "markets_analyzed": len(candidates),
            "candidates_prefilter_passed": len(candidates),
            "setups_created": setup_attempts,
            "setups_after_qc": len(ideas),
            "website_visible_count": len(ranked),
            "telegram_eligible_count": telegram_eligible_count,
            "candle_fetch_errors": fetch_stats.errors,
            "successful_candle_fetches": fetch_stats.successful,
            "failed_candle_symbols": fetch_stats.failed_symbols[:20],
            "setup_block_reasons": setup_block_reason_list,
            "prefilter_passed_markets": top_near_misses,
            "top_rejection_reasons": top_rejection_reasons,
            "last_error": None,
        }
        if debug_scanner_enabled():
            for item in market_debug[:20]:
                logger.info("DEBUG_SCANNER market rejection: %s", item)
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
    global _last_health
    await asyncio.sleep(5)
    while True:
        try:
            await run_scan(exchange="all", timeframe=get_settings().default_timeframe, force=True)
        except Exception:
            _last_health = {
                **_last_health,
                "scanner_running": _background_task is not None and not _background_task.done(),
                "last_scan_finished_at": _now_iso(),
                "last_error": "Background scan failed; check server logs for traceback.",
            }
            logger.exception("Background scan failed")
        await asyncio.sleep(SCAN_INTERVAL_SECONDS)
