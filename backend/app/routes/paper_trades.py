import sqlite3

from fastapi import APIRouter, Depends, HTTPException

from app.models.schemas import PaperTrade, PaperTradeCreate
from app.utils.auth import CurrentUser, current_user
from app.utils.database import get_connection

router = APIRouter()


@router.post("/paper-trade", response_model=PaperTrade)
async def create_paper_trade(payload: PaperTradeCreate, user: CurrentUser = Depends(current_user)):
    if not payload.signal_id or not payload.symbol or not payload.entry_price or not payload.stop_loss:
        raise HTTPException(status_code=422, detail="Missing required trade data.")
    with get_connection() as connection:
        existing = connection.execute(
            "SELECT * FROM paper_trades WHERE user_id = ? AND signal_id = ?",
            (user.id, payload.signal_id),
        ).fetchone()
        if existing:
            return PaperTrade(**{**dict(existing), "already_taken": True})
        try:
            cursor = connection.execute(
                """
                INSERT INTO paper_trades (
                    user_id, signal_id, symbol, timeframe, exchange, direction,
                    entry_price, stop_loss, take_profit_1, take_profit_2, size,
                    risk_reward, setup_score, confidence, market_bias, notes,
                    status, result, taken_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'taken', 'open', CURRENT_TIMESTAMP)
                """,
                (
                    user.id,
                    payload.signal_id,
                    payload.symbol.upper(),
                    payload.timeframe.lower(),
                    payload.exchange.lower(),
                    payload.direction,
                    payload.entry_price,
                    payload.stop_loss,
                    payload.take_profit_1,
                    payload.take_profit_2,
                    payload.size,
                    payload.risk_reward,
                    payload.setup_score,
                    payload.confidence,
                    payload.market_bias,
                    payload.notes,
                ),
            )
        except sqlite3.IntegrityError:
            row = connection.execute(
                "SELECT * FROM paper_trades WHERE user_id = ? AND signal_id = ?",
                (user.id, payload.signal_id),
            ).fetchone()
            if row:
                return PaperTrade(**{**dict(row), "already_taken": True})
            raise
        row = connection.execute("SELECT * FROM paper_trades WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return PaperTrade(**dict(row))


@router.get("/paper-trades", response_model=list[PaperTrade])
async def list_paper_trades(user: CurrentUser = Depends(current_user)):
    with get_connection() as connection:
        rows = connection.execute(
            "SELECT * FROM paper_trades WHERE user_id = ? ORDER BY taken_at DESC, created_at DESC",
            (user.id,),
        ).fetchall()
    return [PaperTrade(**dict(row)) for row in rows]


@router.patch("/paper-trades/{trade_id}", response_model=PaperTrade)
async def update_paper_trade_status(trade_id: int, payload: dict, user: CurrentUser = Depends(current_user)):
    allowed_status = {"taken", "open", "tp_hit", "sl_hit", "closed"}
    allowed_result = {"open", "win", "loss", "closed"}
    status = str(payload.get("status") or "").lower()
    result = str(payload.get("result") or "").lower()
    pnl = payload.get("pnl")
    if status not in allowed_status or result not in allowed_result:
        raise HTTPException(status_code=422, detail="Invalid paper trade status update.")
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE paper_trades
            SET status = ?, result = ?, pnl = ?
            WHERE id = ? AND user_id = ?
            """,
            (status, result, pnl, trade_id, user.id),
        )
        row = connection.execute(
            "SELECT * FROM paper_trades WHERE id = ? AND user_id = ?",
            (trade_id, user.id),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Paper trade not found.")
    return PaperTrade(**dict(row))
