import sqlite3
import json

from fastapi import APIRouter, HTTPException, Query

from app.models.schemas import PaperTrade, PaperTradeCreate
from app.forex.storage import ensure_forex_schema
from app.utils.database import get_connection

router = APIRouter()


def _notes(row: sqlite3.Row) -> dict:
    try:
        return json.loads(row["notes"] or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}


def _normalized_symbol(value: str | None) -> str:
    return str(value or "").upper().replace("/", "").replace("_", "").replace("-", "")


def _normalized_timeframe(value: str | None) -> str:
    aliases = {"15MIN": "15M", "M15": "15M", "H1": "1H", "H4": "4H", "D": "1D", "DAILY": "1D"}
    normalized = str(value or "").upper().replace(" ", "")
    return aliases.get(normalized, normalized)


def _normalized_direction(value: str | None) -> str:
    return "SELL" if str(value or "").upper() in {"SELL", "SHORT"} else "BUY"


def _closest_forex_signal(connection: sqlite3.Connection, trade: sqlite3.Row) -> sqlite3.Row | None:
    notes = _notes(trade)
    source_signal_id = notes.get("source_signal_id")
    if source_signal_id:
        match = connection.execute(
            "SELECT * FROM forex_signals WHERE public_id = ? OR CAST(id AS TEXT) = ? LIMIT 1",
            (str(source_signal_id), str(source_signal_id)),
        ).fetchone()
        if match:
            return match

    candidates = connection.execute(
        "SELECT * FROM forex_signals ORDER BY created_at DESC LIMIT 500"
    ).fetchall()
    symbol = _normalized_symbol(trade["symbol"])
    timeframe = _normalized_timeframe(trade["timeframe"])
    direction = _normalized_direction(trade["direction"])
    matching = [
        row for row in candidates
        if _normalized_symbol(row["symbol"] or row["pair"]) == symbol
        and _normalized_timeframe(row["timeframe"] or row["execution_timeframe"]) == timeframe
        and _normalized_direction(row["direction"]) == direction
    ]
    if not matching:
        return None

    entry = float(trade["entry_price"] or 0)
    stop = float(trade["stop_loss"] or 0)
    return min(
        matching,
        key=lambda row: abs(float(row["entry_price"] or row["entry"] or 0) - entry)
        + abs(float(row["stop_loss"] or 0) - stop),
    )


def _enrich_paper_trade(connection: sqlite3.Connection, row: sqlite3.Row) -> dict:
    result = dict(row)
    if str(row["exchange"] or "").lower() != "forex":
        return result

    signal = _closest_forex_signal(connection, row)
    if not signal:
        return result

    status = str(signal["status"] or "PENDING_ENTRY").upper()
    labels = {
        "PENDING_ENTRY": "Waiting for entry",
        "OPEN": "Open",
        "TP1_HIT": "TP1 Hit",
        "TP2_HIT": "TP2 Hit",
        "STOPPED": "Stop Loss",
        "EXPIRED": "Expired",
        "CANCELLED": "Cancelled",
    }
    lifecycle_result = "open"
    result_r = None
    if status == "TP2_HIT":
        lifecycle_result = "win"
        result_r = float(signal["risk_reward_2"] or signal["rr"] or 0)
    elif status == "STOPPED":
        lifecycle_result = "loss"
        result_r = -1.0
    elif status in {"EXPIRED", "CANCELLED"}:
        lifecycle_result = "closed"

    result.update({
        "source_signal_id": signal["public_id"],
        "lifecycle_status": status,
        "lifecycle_label": labels.get(status, status.replace("_", " ").title()),
        "lifecycle_result": lifecycle_result,
        "result_r": result_r,
        "latest_price": signal["latest_price"] or signal["last_market_price"],
        "latest_price_at": signal["latest_price_at"] or signal["last_price_updated_at"],
        "activated_at": signal["activated_at"],
        "tp1_hit_at": signal["tp1_hit_at"],
        "tp2_hit_at": signal["tp2_hit_at"],
        "stopped_at": signal["stopped_at"],
        "closed_at": signal["closed_at"],
        "expires_at": signal["expires_at"],
        "entry_low": signal["entry_low"],
        "entry_high": signal["entry_high"],
        "risk_reward_1": signal["risk_reward_1"],
        "market_session": signal["market_session"] or signal["session"],
        "setup_reason": signal["setup_reason"] or signal["reason"],
    })
    return result


def _payload_user_id(payload: PaperTradeCreate) -> str:
    if payload.user_id:
        return str(payload.user_id)
    if payload.notes:
        try:
            user_id = json.loads(payload.notes).get("user_id")
            if user_id:
                return str(user_id)
        except (TypeError, json.JSONDecodeError):
            pass
    return "anonymous"


@router.post("/paper-trade", response_model=PaperTrade)
async def create_paper_trade(payload: PaperTradeCreate):
    if not payload.signal_id or not payload.symbol or not payload.entry_price or not payload.stop_loss:
        raise HTTPException(status_code=422, detail="Missing required trade data.")
    user_id = _payload_user_id(payload)
    with get_connection() as connection:
        existing = connection.execute(
            "SELECT * FROM paper_trades WHERE user_id = ? AND signal_id = ?",
            (user_id, payload.signal_id),
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
                    user_id,
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
                (user_id, payload.signal_id),
            ).fetchone()
            if row:
                return PaperTrade(**{**dict(row), "already_taken": True})
            raise
        row = connection.execute("SELECT * FROM paper_trades WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return PaperTrade(**dict(row))


@router.get("/paper-trades", response_model=list[PaperTrade])
async def list_paper_trades(user_id: str | None = Query(default=None)):
    ensure_forex_schema()
    with get_connection() as connection:
        if user_id:
            rows = connection.execute(
                "SELECT * FROM paper_trades WHERE user_id = ? ORDER BY taken_at DESC, created_at DESC",
                (user_id,),
            ).fetchall()
        else:
            rows = connection.execute("SELECT * FROM paper_trades ORDER BY taken_at DESC, created_at DESC").fetchall()
        enriched = [_enrich_paper_trade(connection, row) for row in rows]
    return [PaperTrade(**row) for row in enriched]


@router.get("/paper-trades/{trade_id}", response_model=PaperTrade)
async def get_paper_trade(trade_id: int, user_id: str | None = Query(default=None)):
    ensure_forex_schema()
    with get_connection() as connection:
        if user_id:
            row = connection.execute(
                "SELECT * FROM paper_trades WHERE id = ? AND user_id = ?",
                (trade_id, user_id),
            ).fetchone()
        else:
            row = connection.execute("SELECT * FROM paper_trades WHERE id = ?", (trade_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Paper trade not found.")
        enriched = _enrich_paper_trade(connection, row)
    return PaperTrade(**enriched)


@router.patch("/paper-trades/{trade_id}", response_model=PaperTrade)
async def update_paper_trade_status(trade_id: int, payload: dict, user_id: str | None = Query(default=None)):
    allowed_status = {"taken", "open", "tp_hit", "sl_hit", "closed"}
    allowed_result = {"open", "win", "loss", "closed"}
    status = str(payload.get("status") or "").lower()
    result = str(payload.get("result") or "").lower()
    pnl = payload.get("pnl")
    if status not in allowed_status or result not in allowed_result:
        raise HTTPException(status_code=422, detail="Invalid paper trade status update.")
    with get_connection() as connection:
        if user_id:
            connection.execute(
                """
                UPDATE paper_trades
                SET status = ?, result = ?, pnl = ?
                WHERE id = ? AND user_id = ?
                """,
                (status, result, pnl, trade_id, user_id),
            )
            row = connection.execute(
                "SELECT * FROM paper_trades WHERE id = ? AND user_id = ?",
                (trade_id, user_id),
            ).fetchone()
        else:
            connection.execute(
                """
                UPDATE paper_trades
                SET status = ?, result = ?, pnl = ?
                WHERE id = ?
                """,
                (status, result, pnl, trade_id),
            )
            row = connection.execute("SELECT * FROM paper_trades WHERE id = ?", (trade_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Paper trade not found.")
    return PaperTrade(**dict(row))
