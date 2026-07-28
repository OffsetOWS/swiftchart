from fastapi import APIRouter, HTTPException, Query
import logging

from app.config import SUPPORTED_TIMEFRAMES, get_settings
from app.exchanges.base import MarketDataUnavailable
from app.models.schemas import Candle, Market, RiskSettings
from app.services.alert_dedupe import setup_fingerprint
from app.services.liquidity_filter import skip_low_volume_market
from app.services.market_analysis import (
    MarketAnalysisUnavailable,
    analyze_market_read_only,
    base_asset_symbol as _base_asset_symbol,
    global_regime_score,
    higher_timeframes_for,
    market_for_symbol as _market_for_symbol,
    selected_exchange as _selected_exchange,
    symbol_for_exchange as _symbol_for_exchange,
)
from app.services.market_data import get_candles_cached, get_markets_cached
from app.services.scanner import cached_top_ideas
from app.services.scanner import selected_exchanges as scan_selected_exchanges
from app.services.scanner import trigger_top_ideas_refresh
from app.services.trade_history import save_signal_reviews, save_trade_ideas

router = APIRouter()
logger = logging.getLogger(__name__)
async def _safe_candles(exchange: str, symbol: str, timeframe: str, limit: int):
    return await get_candles_cached(exchange, symbol, timeframe, limit)


async def _skip_low_volume_symbol(exchange: str, symbol: str) -> bool:
    market = await _market_for_symbol(exchange, symbol)
    if market is None:
        logger.info("Skipping %s: perp volume below $100k", symbol.upper())
        return True
    return skip_low_volume_market(market)


def _unique_display_ideas(ideas: list) -> list:
    seen = set()
    unique = []
    for idea in ideas:
        key = setup_fingerprint(idea)
        if key in seen:
            continue
        seen.add(key)
        unique.append(idea)
    return unique


@router.get("/markets", response_model=list[Market])
async def markets(exchange: str = Query(default="hyperliquid")):
    selected_exchange = _selected_exchange(exchange)
    try:
        if selected_exchange == "all":
            markets = []
            for current_exchange in scan_selected_exchanges("all"):
                markets.extend(await get_markets_cached(current_exchange))
            return markets
        return await get_markets_cached(selected_exchange)
    except MarketDataUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Could not fetch markets from %s", selected_exchange)
        raise HTTPException(status_code=502, detail=f"Could not fetch markets from {selected_exchange}. Please try again shortly.") from exc


@router.get("/candles", response_model=list[Candle])
async def candles(
    exchange: str = Query(default="hyperliquid"),
    symbol: str = Query(default="SOLUSDT"),
    timeframe: str = Query(default="4h"),
    limit: int = Query(default=240, ge=50, le=1000),
):
    if timeframe.lower() not in SUPPORTED_TIMEFRAMES:
        raise HTTPException(status_code=400, detail=f"Unsupported timeframe. Use one of: {', '.join(SUPPORTED_TIMEFRAMES)}")
    selected_exchange = _selected_exchange(exchange)
    try:
        candle_exchange = "hyperliquid" if selected_exchange == "all" else selected_exchange
        candle_symbol = _symbol_for_exchange(candle_exchange, symbol)
        df = await _safe_candles(candle_exchange, candle_symbol, timeframe, limit)
        return df.to_dict("records")
    except MarketDataUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Could not fetch candles for %s %s on %s", symbol, timeframe, selected_exchange)
        raise HTTPException(status_code=502, detail="Could not fetch candles. Please try again shortly.") from exc


@router.get("/analyze")
async def analyze(
    exchange: str = Query(default="hyperliquid"),
    symbol: str = Query(default="SOLUSDT"),
    timeframe: str = Query(default="4h"),
    account_size: float | None = None,
    risk_per_trade_pct: float | None = None,
    min_rr: float | None = None,
    max_open_trades: int | None = None,
):
    if timeframe.lower() not in SUPPORTED_TIMEFRAMES:
        raise HTTPException(status_code=400, detail=f"Unsupported timeframe. Use one of: {', '.join(SUPPORTED_TIMEFRAMES)}")
    settings = get_settings()
    risk = RiskSettings(
        account_size=account_size or settings.default_account_size,
        risk_per_trade_pct=risk_per_trade_pct or settings.default_risk_per_trade,
        min_rr=min_rr or settings.default_min_rr,
        max_open_trades=max_open_trades or settings.default_max_open_trades,
        preferred_timeframe=timeframe,
    )
    try:
        try:
            analysis = await analyze_market_read_only(
                exchange=exchange,
                symbol=symbol,
                timeframe=timeframe,
                risk=risk,
            )
        except MarketAnalysisUnavailable as unavailable:
            if not unavailable.found_market:
                base_asset = _base_asset_symbol(symbol)
                raise HTTPException(
                    status_code=422,
                    detail=f"{base_asset or 'This asset'} is not currently available for analysis. Try BTC, ETH, SOL, BNB, or another listed Hyperliquid market.",
                )
            if isinstance(unavailable.last_error, MarketDataUnavailable):
                raise HTTPException(status_code=503, detail=str(unavailable.last_error))
            base_asset = _base_asset_symbol(symbol)
            raise HTTPException(
                status_code=422,
                detail=f"{base_asset or 'This asset'} is not currently available for analysis. Try BTC, ETH, SOL, BNB, or another listed Hyperliquid market.",
            )
        saved_ids = save_trade_ideas(analysis.trade_ideas)
        saved_reviews = save_signal_reviews(analysis.rejected_signals)
        logger.info("Analysis generated %s ideas, rejected %s, and saved %s ideas/%s reviews for %s %s on %s", len(analysis.trade_ideas), len(analysis.rejected_signals), len(saved_ids), saved_reviews, symbol, timeframe, analysis.exchange)
        return analysis
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Could not analyze %s %s on %s", symbol, timeframe, exchange)
        raise HTTPException(status_code=502, detail="Could not analyze symbol. Please try again shortly.") from exc


@router.get("/top-ideas")
async def top_ideas(
    exchange: str = Query(default="hyperliquid"),
    timeframe: str = Query(default="4h"),
    symbols: str | None = Query(default=None, description="Comma-separated symbols"),
):
    if timeframe.lower() not in SUPPORTED_TIMEFRAMES:
        raise HTTPException(status_code=400, detail=f"Unsupported timeframe. Use one of: {', '.join(SUPPORTED_TIMEFRAMES)}")
    selected_exchange = _selected_exchange(exchange)
    result = await cached_top_ideas(selected_exchange, timeframe)
    ideas = _unique_display_ideas(result.get("ideas", []))
    pending_setups = result.get("pending_setups", [])
    if symbols:
        selected_symbols = {item.strip().upper() for item in symbols.split(",") if item.strip()}
        ideas = [idea for idea in ideas if getattr(idea, "symbol", "").upper() in selected_symbols]
        pending_setups = [setup for setup in pending_setups if getattr(setup, "symbol", "").upper() in selected_symbols]
    return {
        **result,
        "ideas": ideas,
        "pending_setups": pending_setups,
    }


@router.post("/top-ideas/refresh")
async def refresh_top_ideas(
    exchange: str = Query(default="hyperliquid"),
    timeframe: str = Query(default="4h"),
):
    if timeframe.lower() not in SUPPORTED_TIMEFRAMES:
        raise HTTPException(status_code=400, detail=f"Unsupported timeframe. Use one of: {', '.join(SUPPORTED_TIMEFRAMES)}")
    return trigger_top_ideas_refresh(_selected_exchange(exchange), timeframe)


@router.get("/top-ideas/refresh")
async def refresh_top_ideas_get(
    exchange: str = Query(default="hyperliquid"),
    timeframe: str = Query(default="4h"),
):
    if timeframe.lower() not in SUPPORTED_TIMEFRAMES:
        raise HTTPException(status_code=400, detail=f"Unsupported timeframe. Use one of: {', '.join(SUPPORTED_TIMEFRAMES)}")
    return trigger_top_ideas_refresh(_selected_exchange(exchange), timeframe)
