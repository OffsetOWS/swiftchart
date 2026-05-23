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
_scan_window_meta: dict[str, dict] = {}
_rotation_history: list[tuple[float, set[str]]] = []
_scan_lock = asyncio.Lock()
_background_task: asyncio.Task | None = None
_scanner_start_count = 0
_last_health: dict = {
    "scanner_running": False,
    "last_scan_started_at": None,
    "last_scan_finished_at": None,
    "last_successful_setup_at": None,
    "last_telegram_sent_at": None,
    "last_non_empty_website_output_at": None,
    "last_watchlist_update_at": None,
    "scanner_restart_count": 0,
    "cache_age_seconds": None,
    "dedup_cache_size": 0,
    "recent_dedup_keys": [],
    "current_scan_window_start": None,
    "current_scan_window_end": None,
    "total_markets_rotated_last_hour": 0,
    "exchange": None,
    "timeframe": None,
    "markets_fetched": 0,
    "markets_analyzed": 0,
    "candidates_prefilter_passed": 0,
    "setups_created": 0,
    "setups_after_qc": 0,
    "ready_setups_count": 0,
    "watchlist_count": 0,
    "watchlist_reasons": [],
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


_PROCESS_STARTED_AT = _now_iso()


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
    key = f"{exchange.lower()}:{timeframe.lower()}"
    if len(markets) <= limit:
        _scan_window_meta[key] = {
            "start": 0,
            "end": len(markets),
            "market_count": len(markets),
            "window_size": len(markets),
            "wrapped": False,
        }
        return markets
    slot = int(time() // SCAN_INTERVAL_SECONDS)
    timeframe_seed = sum(ord(char) for char in timeframe.lower())
    default_start = ((slot + timeframe_seed) * limit) % len(markets)
    start = _scan_offsets.get(key, default_start) % len(markets)
    end = start + limit
    selected = markets[start:end]
    if len(selected) < limit:
        selected.extend(markets[: limit - len(selected)])
    _scan_window_meta[key] = {
        "start": start,
        "end": end % len(markets),
        "market_count": len(markets),
        "window_size": len(selected),
        "wrapped": end > len(markets),
    }
    _scan_offsets[key] = (start + limit) % len(markets)
    return selected


def _record_rotation(markets: list[dict]) -> None:
    global _rotation_history
    now = time()
    symbols = {
        f"{str(market.get('exchange', '')).lower()}:{str(market.get('symbol', '')).upper()}"
        for market in markets
        if market.get("symbol")
    }
    if symbols:
        _rotation_history.append((now, symbols))
    cutoff = now - 3600
    _rotation_history = [(timestamp, item) for timestamp, item in _rotation_history if timestamp >= cutoff]


def _total_markets_rotated_last_hour() -> int:
    cutoff = time() - 3600
    rotated: set[str] = set()
    for timestamp, symbols in _rotation_history:
        if timestamp >= cutoff:
            rotated.update(symbols)
    return len(rotated)


def _current_window_bounds(exchange: str | None, timeframe: str | None) -> tuple[int | None, int | None]:
    if not exchange or not timeframe:
        return None, None
    keys = [f"{selected.lower()}:{timeframe.lower()}" for selected in selected_exchanges(exchange)]
    metas = [_scan_window_meta[key] for key in keys if key in _scan_window_meta]
    if not metas:
        return None, None
    return metas[-1].get("start"), metas[-1].get("end")


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
    regime_block_reason = analysis.market_regime_data.components.get("regime_block_reason")
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
        if regime_block_reason:
            return f"regime is NO_TRADE: {regime_block_reason}"
        return "regime is NO_TRADE"
    if "range is too compressed" in no_trade:
        return "no valid support/resistance zone"
    if "risk/reward is not good enough" in no_trade:
        return "score below 65 or R:R below 2.0"
    return "no setup created"


def _watchlist_label(block_reason: str, entry_status: str | None = None) -> str:
    normalized_status = str(entry_status or "").upper()
    normalized_reason = block_reason.lower()
    if normalized_status == "WAIT_FOR_RETEST" or "retest" in normalized_reason:
        return "Waiting for retest"
    if "unconfirmed sweep" in normalized_reason:
        return "Unconfirmed sweep"
    if "wait" in normalized_reason:
        return "Watching"
    return "Needs confirmation"


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


def _watchlist_candidate_from_analysis(analysis: AnalysisResponse) -> dict | None:
    diagnostic = _near_miss_diagnostic(analysis)
    block_reason = str(diagnostic["block_reason"] or "")
    decision = analysis.market_regime_data.trade_decision
    components = analysis.market_regime_data.components
    raw_score = diagnostic.get("raw_score")
    rr = diagnostic.get("rr")
    position = diagnostic.get("position")
    near_edge = position is None or position <= 0.32 or position >= 0.68
    hard_no_trade = decision == "NO_TRADE" or block_reason.startswith("regime is NO_TRADE")
    dead_or_invalid = components.get("regime_block_reason") in {"low_volatility", "insufficient_structure", "compressed_chop"}
    score_watch = raw_score is not None and 55 <= float(raw_score) < MIN_SETUP_SCORE
    rr_watch = rr is not None and float(rr) >= 1.7
    confirmation_watch = block_reason in {"regime is WAIT", "unconfirmed sweep", "no confirmed trigger", "score below 65"}
    if hard_no_trade or dead_or_invalid or not near_edge:
        return None
    if not (confirmation_watch or score_watch or rr_watch or diagnostic.get("trigger_detected")):
        return None
    return {
        **diagnostic,
        "label": _watchlist_label(block_reason),
        "reason": block_reason,
        "timeframe": analysis.timeframe,
        "exchange": analysis.exchange,
        "current_price": round(float(analysis.current_price), 8),
    }


def _watchlist_candidate_from_idea(idea: TradeIdea) -> dict | None:
    score = _idea_score(idea)
    rr = float(idea.risk_reward_ratio)
    entry_status = str(idea.entry_status)
    if entry_status == "REJECTED_EXHAUSTED" or idea.exhaustion_risk == "High" or idea.regime_trade_decision == "NO_TRADE":
        return None
    if entry_status == "READY" and score >= MIN_SETUP_SCORE and rr >= 2.0:
        return None
    reason = "Waiting for retest" if entry_status == "WAIT_FOR_RETEST" else "Needs confirmation"
    if not (entry_status == "WAIT_FOR_RETEST" or 55 <= score < MIN_SETUP_SCORE or 1.7 <= rr < 2.0):
        return None
    return {
        "symbol": idea.symbol,
        "timeframe": idea.timeframe,
        "exchange": idea.exchange,
        "regime": idea.regime_type or idea.market_regime,
        "decision": idea.regime_trade_decision or "WAIT",
        "direction": idea.direction,
        "position": None,
        "nearest_support": None,
        "nearest_resistance": None,
        "trigger_detected": bool(idea.reversal_confirmations),
        "raw_score": round(score, 1),
        "rr": round(rr, 2),
        "block_reason": reason,
        "label": _watchlist_label(reason, entry_status),
        "reason": reason,
        "entry_status": entry_status,
        "current_price": None,
    }


def _bot_state_path() -> Path:
    return Path(os.getenv("BOT_STATE_PATH", ".swiftchart_bot_state.json"))


def _load_bot_state() -> dict:
    path = _bot_state_path()
    if not path.exists():
        return {"subscribers": [], "sent_alerts": [], "alert_dedupe": {}}
    try:
        data = json.loads(path.read_text())
        return {
            "subscribers": data.get("subscribers", []),
            "sent_alerts": data.get("sent_alerts", []),
            "alert_dedupe": data.get("alert_dedupe", {}),
        }
    except (OSError, json.JSONDecodeError):
        return {"subscribers": [], "sent_alerts": [], "alert_dedupe": {}}


def _dedupe_health_snapshot() -> dict:
    data = _load_bot_state()
    sent_alerts = list(data.get("sent_alerts", []))
    alert_dedupe = data.get("alert_dedupe", {}) if isinstance(data.get("alert_dedupe", {}), dict) else {}
    recent_keys: list[dict] = []
    last_telegram_sent_at: str | None = None
    dedupe_size = len(sent_alerts)

    for namespace, payload in alert_dedupe.items():
        if not isinstance(payload, dict):
            continue
        keys = payload.get("keys", {})
        fingerprints = payload.get("fingerprints", {})
        if isinstance(keys, dict):
            dedupe_size += len(keys)
            for key, item in keys.items():
                if not isinstance(item, dict):
                    continue
                last_alert_time = item.get("last_alert_time")
                if namespace == "telegram" and last_alert_time:
                    if last_telegram_sent_at is None or str(last_alert_time) > last_telegram_sent_at:
                        last_telegram_sent_at = str(last_alert_time)
                recent_keys.append(
                    {
                        "namespace": namespace,
                        "key": key,
                        "last_alert_time": last_alert_time,
                        "latest_candle_time": item.get("latest_candle_time"),
                    }
                )
        if isinstance(fingerprints, dict):
            dedupe_size += len(fingerprints)

    recent_keys = sorted(
        recent_keys,
        key=lambda item: str(item.get("last_alert_time") or ""),
        reverse=True,
    )[:10]
    if len(recent_keys) < 10:
        recent_keys.extend(
            {"namespace": "legacy", "key": key}
            for key in sent_alerts[-(10 - len(recent_keys)) :]
        )

    return {
        "last_telegram_sent_at": last_telegram_sent_at,
        "dedup_cache_size": dedupe_size,
        "recent_dedup_keys": recent_keys[:10],
    }


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


def _cache_age_seconds(exchange: str | None, timeframe: str | None) -> int | None:
    if not exchange or not timeframe:
        return None
    key = (normalize_exchange(exchange), timeframe.lower())
    cached = _scan_cache.get(key)
    if cached is None and normalize_exchange(exchange) != "all":
        cached = _scan_cache.get(("all", timeframe.lower()))
    if cached is None:
        return None
    return max(0, int(monotonic() - cached[0]))


def scanner_health() -> dict:
    exchange = _last_health.get("exchange")
    timeframe = _last_health.get("timeframe")
    window_start, window_end = _current_window_bounds(exchange, timeframe)
    dedupe_snapshot = _dedupe_health_snapshot()
    return {
        **_last_health,
        "scanner_running": _background_task is not None and not _background_task.done(),
        "process_started_at": _PROCESS_STARTED_AT,
        "cache_age_seconds": _cache_age_seconds(exchange, timeframe),
        "current_scan_window_start": window_start,
        "current_scan_window_end": window_end,
        "total_markets_rotated_last_hour": _total_markets_rotated_last_hour(),
        **dedupe_snapshot,
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
        watchlist_reasons: Counter[str] = Counter()
        market_debug: list[dict] = []
        near_misses: list[dict] = []
        watchlist: list[dict] = []
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
        _record_rotation(scan_markets)
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
                            watchlist_candidate = _watchlist_candidate_from_idea(idea)
                            if watchlist_candidate:
                                watchlist.append(watchlist_candidate)
                                watchlist_reasons[watchlist_candidate["reason"]] += 1
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
                        watchlist_candidate = _watchlist_candidate_from_analysis(analysis)
                        if watchlist_candidate:
                            watchlist.append(watchlist_candidate)
                            watchlist_reasons[watchlist_candidate["reason"]] += 1
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
                    return [
                        idea
                        for idea in analysis.trade_ideas
                        if (idea.setup_score or idea.confidence_score) >= MIN_SETUP_SCORE
                        and idea.risk_reward_ratio >= risk.min_rr
                        and idea.entry_status == "READY"
                    ]
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
        watchlist_reason_list = [
            {"reason": reason, "count": count}
            for reason, count in watchlist_reasons.most_common(12)
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
        watchlist_ranked = sorted(
            watchlist,
            key=lambda item: (
                float(item.get("raw_score") or 0),
                float(item.get("rr") or 0),
                bool(item.get("trigger_detected")),
            ),
            reverse=True,
        )[:12]
        finished_at = _now_iso()
        last_successful_setup_at = _last_health.get("last_successful_setup_at")
        if ideas:
            last_successful_setup_at = finished_at
        last_non_empty_website_output_at = _last_health.get("last_non_empty_website_output_at")
        if ranked:
            last_non_empty_website_output_at = finished_at
        last_watchlist_update_at = _last_health.get("last_watchlist_update_at")
        if watchlist_ranked:
            last_watchlist_update_at = finished_at
        window_start, window_end = _current_window_bounds(selected_exchange, timeframe)
        dedupe_snapshot = _dedupe_health_snapshot()
        result = {
            "timeframe": timeframe,
            "exchange": selected_exchange,
            "ideas": ranked,
            "watchlist": watchlist_ranked,
            "errors": [],
            "message": None if len(ranked) >= 5 else f"Only {len(ranked)} valid setups found. Other coins are currently no-trade.",
            "scan_stats": {
                "markets": len(markets),
                "scan_window": len(scan_markets),
                "filtered": len(candidates),
                "analyzed": len(candidates),
                "valid_setups": len(ranked),
                "ready_setups": len(ranked),
                "watchlist_count": len(watchlist_ranked),
                "watchlist_reasons": watchlist_reason_list,
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
            "last_scan_finished_at": finished_at,
            "last_successful_setup_at": last_successful_setup_at,
            "last_telegram_sent_at": dedupe_snapshot["last_telegram_sent_at"],
            "last_non_empty_website_output_at": last_non_empty_website_output_at,
            "last_watchlist_update_at": last_watchlist_update_at,
            "scanner_restart_count": max(0, _scanner_start_count - 1),
            "cache_age_seconds": None,
            "dedup_cache_size": dedupe_snapshot["dedup_cache_size"],
            "recent_dedup_keys": dedupe_snapshot["recent_dedup_keys"],
            "current_scan_window_start": window_start,
            "current_scan_window_end": window_end,
            "total_markets_rotated_last_hour": _total_markets_rotated_last_hour(),
            "exchange": selected_exchange,
            "timeframe": timeframe,
            "markets_fetched": len(markets),
            "markets_analyzed": len(candidates),
            "candidates_prefilter_passed": len(candidates),
            "setups_created": setup_attempts,
            "setups_after_qc": len(ideas),
            "ready_setups_count": len(ranked),
            "watchlist_count": len(watchlist_ranked),
            "watchlist_reasons": watchlist_reason_list,
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
    global _background_task, _scanner_start_count
    if _background_task is not None and not _background_task.done():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    _scanner_start_count += 1
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
