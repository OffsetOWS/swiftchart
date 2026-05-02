from fastapi import APIRouter

from app.models.schemas import PaperTrade, PaperTradeCreate
from app.utils.database import get_connection

router = APIRouter()


@router.post("/paper-trade", response_model=PaperTrade)
async def create_paper_trade(payload: PaperTradeCreate):
    with get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO paper_trades (
                symbol, timeframe, exchange, direction, entry_price, stop_loss,
                take_profit_1, take_profit_2, size, notes
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload.symbol.upper(),
                payload.timeframe,
                payload.exchange,
                payload.direction,
                payload.entry_price,
                payload.stop_loss,
                payload.take_profit_1,
                payload.take_profit_2,
                payload.size,
                payload.notes,
            ),
        )
        row = connection.execute("SELECT * FROM paper_trades WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return PaperTrade(**dict(row))


@router.get("/paper-trades", response_model=list[PaperTrade])
async def list_paper_trades():
    with get_connection() as connection:
        rows = connection.execute("SELECT * FROM paper_trades ORDER BY created_at DESC").fetchall()
    return [PaperTrade(**dict(row)) for row in rows]
