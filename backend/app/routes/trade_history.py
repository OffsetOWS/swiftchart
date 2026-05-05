from fastapi import APIRouter, HTTPException, Query

from app.models.schemas import TradeHistoryPage, TradeHistoryRecord, TradeStats
from app.services.trade_history import check_trade_outcomes, get_trade_history, query_trade_history, stats

router = APIRouter()


@router.get("/trade-history", response_model=TradeHistoryPage)
async def trade_history(
    symbol: str | None = Query(default=None),
    timeframe: str | None = Query(default=None),
    exchange: str | None = Query(default=None),
    direction: str | None = Query(default=None),
    status: str | None = Query(default=None),
    result: str | None = Query(default=None),
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=250),
    sort: str = Query(default="desc", pattern="^(asc|desc)$"),
):
    return query_trade_history(
        {
            "symbol": symbol,
            "timeframe": timeframe,
            "exchange": None if exchange == "all" else exchange,
            "direction": direction,
            "status": status,
            "result": result,
            "date_from": date_from,
            "date_to": date_to,
        },
        page=page,
        limit=limit,
        sort=sort,
    )


@router.get("/trade-history/{trade_id}", response_model=TradeHistoryRecord)
async def trade_history_detail(trade_id: int):
    record = get_trade_history(trade_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Trade idea not found.")
    return record


@router.post("/trade-history/check")
async def trade_history_check():
    return await check_trade_outcomes()


@router.get("/trade-stats", response_model=TradeStats)
async def trade_stats():
    return stats()
