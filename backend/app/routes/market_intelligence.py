from fastapi import APIRouter, Query

from app.services.cmc import get_market_intelligence

router = APIRouter()


@router.get("/market-intelligence")
async def market_intelligence(
    symbols: str | None = Query(default=None, description="Comma-separated signal symbols"),
):
    requested = [item.strip() for item in (symbols or "").split(",") if item.strip()]
    return await get_market_intelligence(requested)
