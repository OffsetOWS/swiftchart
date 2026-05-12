from fastapi import APIRouter, HTTPException, Query
import logging

from app.config import DEFAULT_SCAN_LIST, SUPPORTED_TIMEFRAMES, get_settings
from app.models.schemas import Candle, Market, RiskSettings
from app.services.market_data import get_candles_cached, get_markets_cached
from app.services.scanner import cached_top_ideas
from app.services.trade_history import save_signal_reviews, save_trade_ideas
from app.strategy.market_regime import regime_score_from_dataframe
from app.strategy.trade_ideas import analyze_dataframe

router = APIRouter()
logger = logging.getLogger(__name__)


def _selected_exchange(exchange: str | None) -> str:
    normalized = (exchange or get_settings().default_exchange).lower()
    if normalized == "all":
        return "hyperliquid"
    return normalized


async def _safe_candles(exchange: str, symbol: str, timeframe: str, limit: int):
    return await get_candles_cached(exchange, symbol, timeframe, limit)


async def _market_scan_symbols(exchange: str) -> list[str]:
    return DEFAULT_SCAN_LIST


def higher_timeframes_for(timeframe: str) -> list[str]:
    normalized = timeframe.lower()
    if normalized in {"30m", "1h"}:
        return ["4h", "1d"]
    if normalized in {"2h", "4h", "6h", "8h", "12h"}:
        return ["1d"]
    return []


async def global_regime_score(exchange: str, timeframe: str) -> float | None:
    scores = []
    for symbol in ("BTCUSDT", "ETHUSDT"):
        try:
            df = await get_candles_cached(exchange, symbol, timeframe, 260)
            if len(df) >= 80:
                scores.append(regime_score_from_dataframe(df))
        except Exception:
            continue
    if not scores:
        return None
    return round(sum(scores) / len(scores), 1)


@router.get("/markets", response_model=list[Market])
async def markets(exchange: str = Query(default="hyperliquid")):
    selected_exchange = _selected_exchange(exchange)
    try:
        return await get_markets_cached(selected_exchange)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not fetch markets from {selected_exchange}: {exc}") from exc


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
        df = await _safe_candles(selected_exchange, symbol, timeframe, limit)
        return df.to_dict("records")
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not fetch candles: {exc}") from exc


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
        exchanges = [_selected_exchange(exchange)]
        last_error = None
        analysis = None
        for selected_exchange in exchanges:
            try:
                df = await get_candles_cached(selected_exchange, symbol, timeframe, 320)
                if len(df) < 80:
                    last_error = "Not enough candle history for analysis."
                    continue
                htf_dfs = []
                for htf in higher_timeframes_for(timeframe):
                    try:
                        htf_dfs.append(await get_candles_cached(selected_exchange, symbol, htf, 240))
                    except Exception:
                        continue
                analysis = analyze_dataframe(
                    symbol.upper(),
                    timeframe,
                    selected_exchange,
                    df,
                    risk,
                    htf_dfs,
                    global_regime_score=await global_regime_score(selected_exchange, timeframe),
                )
                break
            except Exception as exc:
                last_error = exc
                continue
        if analysis is None:
            raise HTTPException(status_code=422, detail=str(last_error or "Could not analyze symbol."))
        saved_ids = save_trade_ideas(analysis.trade_ideas)
        saved_reviews = save_signal_reviews(analysis.rejected_signals)
        logger.info("Analysis generated %s ideas, rejected %s, and saved %s ideas/%s reviews for %s %s on %s", len(analysis.trade_ideas), len(analysis.rejected_signals), len(saved_ids), saved_reviews, symbol, timeframe, analysis.exchange)
        return analysis
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not analyze symbol: {exc}") from exc


@router.get("/top-ideas")
async def top_ideas(
    exchange: str = Query(default="hyperliquid"),
    timeframe: str = Query(default="4h"),
    symbols: str | None = Query(default=None, description="Comma-separated symbols"),
):
    if timeframe.lower() not in SUPPORTED_TIMEFRAMES:
        raise HTTPException(status_code=400, detail=f"Unsupported timeframe. Use one of: {', '.join(SUPPORTED_TIMEFRAMES)}")
    selected_exchange = _selected_exchange(exchange)
    if symbols is None:
        return await cached_top_ideas(selected_exchange, timeframe)

    selected_exchanges = [selected_exchange]
    settings = get_settings()
    risk = RiskSettings(
        account_size=settings.default_account_size,
        risk_per_trade_pct=settings.default_risk_per_trade,
        min_rr=settings.default_min_rr,
        max_open_trades=settings.default_max_open_trades,
        preferred_timeframe=timeframe,
    )
    ideas = []
    errors = []
    for selected_exchange in selected_exchanges:
        scan_symbols = [item.strip().upper() for item in symbols.split(",")] if symbols else await _market_scan_symbols(selected_exchange)
        for symbol in scan_symbols:
            try:
                df = await get_candles_cached(selected_exchange, symbol, timeframe, 260)
                if len(df) >= 80:
                    htf_dfs = []
                    for htf in higher_timeframes_for(timeframe):
                        try:
                            htf_dfs.append(await get_candles_cached(selected_exchange, symbol, htf, 220))
                        except Exception:
                            continue
                    analysis = analyze_dataframe(
                        symbol,
                        timeframe,
                        selected_exchange,
                        df,
                        risk,
                        htf_dfs,
                        global_regime_score=await global_regime_score(selected_exchange, timeframe),
                    )
                    ideas.extend(analysis.trade_ideas)
                    save_signal_reviews(analysis.rejected_signals)
            except Exception as exc:
                errors.append({"exchange": selected_exchange, "symbol": symbol, "error": str(exc)})
    ranked = sorted(ideas, key=lambda idea: idea.rank_score, reverse=True)[:5]
    saved_ids = save_trade_ideas(ranked)
    logger.info("Top ideas generated %s ranked ideas and saved %s for exchange=%s timeframe=%s", len(ranked), len(saved_ids), exchange, timeframe)
    return {
        "timeframe": timeframe,
        "exchange": exchange,
        "ideas": ranked,
        "errors": errors,
        "message": None
        if len(ranked) >= 5
        else f"Only {len(ranked)} valid setups found. Other coins are currently no-trade.",
    }
