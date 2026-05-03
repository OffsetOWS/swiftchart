from fastapi import APIRouter, HTTPException, Query

from app.config import DEFAULT_SCAN_LIST, SUPPORTED_TIMEFRAMES, get_settings
from app.exchanges.factory import get_exchange
from app.models.schemas import Candle, Market, RiskSettings
from app.strategy.trade_ideas import analyze_dataframe

router = APIRouter()


def higher_timeframes_for(timeframe: str) -> list[str]:
    normalized = timeframe.lower()
    if normalized in {"30m", "1h"}:
        return ["4h", "1d"]
    if normalized in {"2h", "4h", "6h", "8h", "12h"}:
        return ["1d"]
    return []


@router.get("/markets", response_model=list[Market])
async def markets(exchange: str = Query(default="binance")):
    try:
        client = get_exchange(exchange)
        return await client.get_markets()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not fetch markets from {exchange}: {exc}") from exc


@router.get("/candles", response_model=list[Candle])
async def candles(
    exchange: str = Query(default="binance"),
    symbol: str = Query(default="SOLUSDT"),
    timeframe: str = Query(default="4h"),
    limit: int = Query(default=240, ge=50, le=1000),
):
    if timeframe.lower() not in SUPPORTED_TIMEFRAMES:
        raise HTTPException(status_code=400, detail=f"Unsupported timeframe. Use one of: {', '.join(SUPPORTED_TIMEFRAMES)}")
    try:
        client = get_exchange(exchange)
        df = await client.get_candles(symbol, timeframe, limit)
        return df.to_dict("records")
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not fetch candles: {exc}") from exc


@router.get("/analyze")
async def analyze(
    exchange: str = Query(default="binance"),
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
        client = get_exchange(exchange)
        df = await client.get_candles(symbol, timeframe, 320)
        if len(df) < 80:
            raise HTTPException(status_code=422, detail="Not enough candle history for analysis.")
        htf_dfs = []
        for htf in higher_timeframes_for(timeframe):
            try:
                htf_dfs.append(await client.get_candles(symbol, htf, 240))
            except Exception:
                continue
        return analyze_dataframe(symbol.upper(), timeframe, exchange, df, risk, htf_dfs)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not analyze symbol: {exc}") from exc


@router.get("/top-ideas")
async def top_ideas(
    exchange: str = Query(default="binance"),
    timeframe: str = Query(default="4h"),
    symbols: str | None = Query(default=None, description="Comma-separated symbols"),
):
    scan_symbols = [item.strip().upper() for item in symbols.split(",")] if symbols else DEFAULT_SCAN_LIST
    client = get_exchange(exchange)
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
    for symbol in scan_symbols:
        try:
            df = await client.get_candles(symbol, timeframe, 260)
            if len(df) >= 80:
                htf_dfs = []
                for htf in higher_timeframes_for(timeframe):
                    try:
                        htf_dfs.append(await client.get_candles(symbol, htf, 220))
                    except Exception:
                        continue
                analysis = analyze_dataframe(symbol, timeframe, exchange, df, risk, htf_dfs)
                ideas.extend(analysis.trade_ideas)
        except Exception as exc:
            errors.append({"symbol": symbol, "error": str(exc)})
    ranked = sorted(ideas, key=lambda idea: idea.rank_score, reverse=True)[:5]
    return {
        "timeframe": timeframe,
        "exchange": exchange,
        "ideas": ranked,
        "errors": errors,
        "message": None
        if len(ranked) >= 5
        else f"Only {len(ranked)} valid setups found. Other coins are currently no-trade.",
    }
