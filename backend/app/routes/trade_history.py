from fastapi import APIRouter, HTTPException, Query

from app.models.schemas import TradeHistoryRecord, TradeStats
from app.services.trade_history import check_trade_outcomes, get_trade_history, list_trade_history, stats

router = APIRouter()


@router.get("/trade-history", response_model=list[TradeHistoryRecord])
async def trade_history(
    symbol: str | None = Query(default=None),
    timeframe: str | None = Query(default=None),
    direction: str | None = Query(default=None),
    status: str | None = Query(default=None),
    result: str | None = Query(default=None),
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
):
    return list_trade_history(
        {
            "symbol": symbol,
            "timeframe": timeframe,
            "direction": direction,
            "status": status,
            "result": result,
            "date_from": date_from,
            "date_to": date_to,
        }
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
