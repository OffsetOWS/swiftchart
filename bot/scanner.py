import logging
import os

import pandas as pd

from app.config import DEFAULT_SCAN_LIST, get_settings
from app.exchanges.factory import get_exchange
from app.models.schemas import RiskSettings, TradeIdea
from app.services.liquidity_filter import perp_volume_24h, filter_liquid_perp_markets
from app.services.signal_limits import cap_signals_per_scan
from app.services.market_data import get_candles_cached, get_markets_cached
from app.services.scanner import btc_regime_from_scores
from app.strategy.market_regime import regime_score_from_dataframe
from app.strategy.trade_ideas import analyze_dataframe

logger = logging.getLogger(__name__)

DEFAULT_TELEGRAM_SCAN_SYMBOL_LIMIT = 80


def _selected_exchange(exchange: str | None = None) -> str:
    settings = get_settings()
    selected = (exchange or settings.default_exchange).lower()
    if selected == "all":
        logger.info("telegram_independent_scan_exchange_all_fallback exchange=%s", settings.default_exchange)
        return settings.default_exchange
    return selected


def _risk(timeframe: str) -> RiskSettings:
    settings = get_settings()
    return RiskSettings(
        account_size=settings.default_account_size,
        risk_per_trade_pct=settings.default_risk_per_trade,
        min_rr=settings.default_min_rr,
        max_open_trades=settings.default_max_open_trades,
        preferred_timeframe=timeframe,
    )


def _scan_symbol_limit() -> int:
    raw = os.getenv("TELEGRAM_SCAN_SYMBOL_LIMIT", str(DEFAULT_TELEGRAM_SCAN_SYMBOL_LIMIT))
    try:
        value = int(raw)
    except ValueError:
        value = DEFAULT_TELEGRAM_SCAN_SYMBOL_LIMIT
    return max(50, min(100, value))


async def _scan_symbols(exchange: str) -> list[str]:
    limit = _scan_symbol_limit()
    try:
        markets = await get_markets_cached(exchange)
        active_markets = filter_liquid_perp_markets([market for market in markets if market.get("active", True)])
        ranked = sorted(active_markets, key=lambda market: perp_volume_24h(market) or 0, reverse=True)
        symbols = [str(market.get("symbol", "")).upper() for market in ranked if market.get("symbol")]
        if symbols:
            return list(dict.fromkeys(symbols))[:limit]
    except Exception as exc:
        logger.warning("Telegram independent market discovery failed exchange=%s error=%s", exchange, exc)
    # No verified volume means no safe candidate; do not bypass the floor.
    return []


async def _get_alert_candles(exchange: str, symbol: str, timeframe: str, limit: int):
    if timeframe.lower() != "3h":
        return await get_candles_cached(exchange, symbol, timeframe, limit)

    df = await get_candles_cached(exchange, symbol, "1h", limit * 3)
    if "timestamp" not in df.columns:
        return df

    candles = df.copy()
    candles["timestamp"] = pd.to_datetime(candles["timestamp"], utc=True)
    candles = candles.set_index("timestamp").sort_index()
    resampled = (
        candles.resample("3h", label="right", closed="right")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
        .dropna()
        .tail(limit)
        .reset_index()
    )
    return resampled[["timestamp", "open", "high", "low", "close", "volume"]]


async def _btc_context(exchange: str) -> dict | None:
    btc_4h = btc_1d = eth_4h = None
    try:
        btc_4h = await get_candles_cached(exchange, "BTCUSDT", "4h", 220)
    except Exception as exc:
        logger.info("Telegram BTC 4H context unavailable exchange=%s error=%s", exchange, exc)
    try:
        btc_1d = await get_candles_cached(exchange, "BTCUSDT", "1d", 220)
    except Exception as exc:
        logger.info("Telegram BTC 1D context unavailable exchange=%s error=%s", exchange, exc)
    eth_4h = None
    if get_settings().bounce_long_market_filter_mode.upper() == "F2":
        try:
            eth_4h = await get_candles_cached(exchange, "ETHUSDT", "4h", 220)
        except Exception as exc:
            logger.info("Telegram ETH 4H context unavailable exchange=%s error=%s", exchange, exc)
    from app.strategy.bounce_market_filter import build_btc_market_context, completed_candles
    if btc_4h is None or btc_1d is None:
        return None
    context = build_btc_market_context(
        completed_candles(btc_4h, "4h"), completed_candles(btc_1d, "1d"),
        completed_candles(eth_4h, "4h") if eth_4h is not None else None,
    )
    score_4h = regime_score_from_dataframe(completed_candles(btc_4h, "4h"))
    score_1d = regime_score_from_dataframe(completed_candles(btc_1d, "1d"))
    return {**btc_regime_from_scores(score_4h, score_1d), **context}


async def scan_top_ideas(timeframe: str, exchange: str | None = None) -> tuple[list[TradeIdea], str, dict]:
    """Non-authoritative crypto scan retained for manual commands and diagnostics.

    Actionable Telegram alerts consume persisted canonical V2 opportunities through
    ``bot.alerts.run_alert_scan`` and must never dispatch from this result.
    """
    selected_exchange = _selected_exchange(exchange)
    get_exchange(selected_exchange)
    risk = _risk(timeframe)
    ideas: list[TradeIdea] = []
    rejection_reasons: dict[str, int] = {}
    symbols_scanned = 0
    symbols = await _scan_symbols(selected_exchange)
    market_metadata = {str(m.get("symbol", "")).upper():m for m in await get_markets_cached(selected_exchange)}
    btc_context = await _btc_context(selected_exchange)

    logger.info(
        "telegram_independent_scan_started exchange=%s timeframe=%s symbols=%s",
        selected_exchange,
        timeframe,
        len(symbols),
    )

    for symbol in symbols:
        try:
            df = await _get_alert_candles(selected_exchange, symbol, timeframe, 260)
            symbols_scanned += 1
            if len(df) < 80:
                rejection_reasons["insufficient candles"] = rejection_reasons.get("insufficient candles", 0) + 1
                continue
            df.attrs["market_metadata"] = market_metadata.get(symbol,{})
            if btc_context is not None:
                df.attrs["btc_market_context"] = btc_context
            analysis = analyze_dataframe(symbol, timeframe, selected_exchange, df, risk)
            for idea in analysis.trade_ideas: idea.market_volume_24h = perp_volume_24h(market_metadata.get(symbol,{}))
            from app.services.trade_history import save_signal_reviews
            save_signal_reviews(analysis.rejected_signals)
            ideas.extend(analysis.trade_ideas)
            for review in analysis.rejected_signals:
                reason = review.reason or "rejected by setup/QC"
                rejection_reasons[reason] = rejection_reasons.get(reason, 0) + 1
            if not analysis.trade_ideas and not analysis.rejected_signals:
                reason = analysis.no_trade_reason or "no setup created"
                rejection_reasons[reason] = rejection_reasons.get(reason, 0) + 1
        except Exception as exc:
            reason = f"scan error: {type(exc).__name__}"
            rejection_reasons[reason] = rejection_reasons.get(reason, 0) + 1
            logger.warning("Telegram independent scan failed symbol=%s exchange=%s timeframe=%s error=%s", symbol, selected_exchange, timeframe, exc)

    from app.services.signal_limits import rank_by_volume
    ranked = rank_by_volume(ideas)
    ranked = cap_signals_per_scan(ranked)
    logger.info(
        "telegram_independent_scan_completed exchange=%s timeframe=%s symbols_scanned=%s valid_ideas_found=%s rejection_reasons=%s",
        selected_exchange,
        timeframe,
        symbols_scanned,
        len(ranked),
        rejection_reasons,
    )
    return ranked, selected_exchange, {
        "symbols_scanned": symbols_scanned,
        "valid_ideas_found": len(ranked),
        "rejection_reasons": rejection_reasons,
        "btc_context": btc_context,
    }
