from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException

from app.config import get_settings
from app.models.api import SignalSummary, TakeTradeRequest, TradeUpdateRequest, UserTakenTrade
from app.utils.auth import CurrentUser
from app.utils.database import get_connection


def row_to_signal(row: sqlite3.Row | dict[str, Any]) -> SignalSummary:
    data = dict(row)
    return SignalSummary(
        id=int(data["id"]),
        symbol=data["symbol"],
        timeframe=data["timeframe"],
        exchange=data["exchange"],
        direction=data["direction"],
        setup_score=data.get("setup_score"),
        setup_grade=data.get("setup_grade"),
        entry_zone=(float(data["entry_zone_low"]), float(data["entry_zone_high"])),
        stop_loss=float(data["stop_loss"]),
        take_profit_1=float(data["take_profit_1"]),
        take_profit_2=float(data["take_profit_2"]),
        risk_reward=float(data["risk_reward"]),
        confidence=float(data["confidence"]),
        status=data["status"],
        result=data["result"],
        reason=data["reason"],
        invalidation=data["invalidation"],
        created_at=data["created_at"],
    )


def list_signals(limit: int = 50, symbol: str | None = None) -> list[SignalSummary]:
    clauses = []
    values: list[Any] = []
    if symbol:
        clauses.append("symbol = ?")
        values.append(symbol.upper())
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with get_connection() as connection:
        rows = connection.execute(
            f"SELECT * FROM trade_ideas {where} ORDER BY created_at DESC, id DESC LIMIT ?",
            [*values, min(max(1, limit), 250)],
        ).fetchall()
    return [row_to_signal(row) for row in rows]


def get_signal(signal_id: int) -> SignalSummary | None:
    with get_connection() as connection:
        row = connection.execute("SELECT * FROM trade_ideas WHERE id = ?", (signal_id,)).fetchone()
    return row_to_signal(row) if row else None


def ensure_profile(user: CurrentUser) -> tuple[dict[str, Any], bool]:
    with get_connection() as connection:
        row = connection.execute("SELECT * FROM profiles WHERE user_id = ?", (user.id,)).fetchone()
        if row:
            connection.execute(
                "UPDATE profiles SET email = COALESCE(?, email), updated_at = CURRENT_TIMESTAMP WHERE user_id = ?",
                (user.email, user.id),
            )
            row = connection.execute("SELECT * FROM profiles WHERE user_id = ?", (user.id,)).fetchone()
            return dict(row), False
        connection.execute(
            "INSERT INTO profiles (user_id, email) VALUES (?, ?)",
            (user.id, user.email),
        )
        row = connection.execute("SELECT * FROM profiles WHERE user_id = ?", (user.id,)).fetchone()
    return dict(row), True


def row_to_taken_trade(row: sqlite3.Row | dict[str, Any], *, already_taken: bool = False) -> UserTakenTrade:
    data = dict(row)
    return UserTakenTrade(**{**data, "already_taken": already_taken})


def _validate_take_trade_payload(payload: TakeTradeRequest) -> None:
    direction = payload.direction.lower()
    if direction == "long" and not (payload.stop_loss < payload.entry_price < payload.take_profit_1 <= payload.take_profit_2):
        raise HTTPException(status_code=422, detail="Invalid long signal levels.")
    if direction == "short" and not (payload.stop_loss > payload.entry_price > payload.take_profit_1 >= payload.take_profit_2):
        raise HTTPException(status_code=422, detail="Invalid short signal levels.")
    if payload.signal_timestamp:
        timestamp = payload.signal_timestamp if payload.signal_timestamp.tzinfo else payload.signal_timestamp.replace(tzinfo=UTC)
        age_minutes = (datetime.now(UTC) - timestamp.astimezone(UTC)).total_seconds() / 60
        if age_minutes > get_settings().signal_max_age_minutes:
            raise HTTPException(status_code=409, detail="Signal is stale. Refresh before taking this trade.")
    liquidity = (payload.liquidity_status or "").lower()
    if "low liquidity" in liquidity:
        raise HTTPException(status_code=409, detail="Signal liquidity is too low to save as taken.")


def take_trade(user: CurrentUser, payload: TakeTradeRequest) -> UserTakenTrade:
    _validate_take_trade_payload(payload)
    with get_connection() as connection:
        existing = connection.execute(
            "SELECT * FROM user_taken_trades WHERE user_id = ? AND signal_id = ?",
            (user.id, payload.signal_id),
        ).fetchone()
        if existing:
            return row_to_taken_trade(existing, already_taken=True)
        try:
            cursor = connection.execute(
                """
                INSERT INTO user_taken_trades (
                    user_id, signal_id, symbol, timeframe, exchange, direction,
                    entry_price, stop_loss, take_profit_1, take_profit_2,
                    risk_reward, setup_score, confidence, market_bias,
                    market_regime, liquidity_status, signal_timestamp
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    payload.risk_reward,
                    payload.setup_score,
                    payload.confidence,
                    payload.market_bias,
                    payload.market_regime,
                    payload.liquidity_status,
                    payload.signal_timestamp.isoformat() if payload.signal_timestamp else None,
                ),
            )
        except sqlite3.IntegrityError:
            row = connection.execute(
                "SELECT * FROM user_taken_trades WHERE user_id = ? AND signal_id = ?",
                (user.id, payload.signal_id),
            ).fetchone()
            if row:
                return row_to_taken_trade(row, already_taken=True)
            raise
        row = connection.execute("SELECT * FROM user_taken_trades WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return row_to_taken_trade(row)


def list_user_trades(user: CurrentUser) -> list[UserTakenTrade]:
    with get_connection() as connection:
        rows = connection.execute(
            "SELECT * FROM user_taken_trades WHERE user_id = ? ORDER BY taken_at DESC, id DESC",
            (user.id,),
        ).fetchall()
    return [row_to_taken_trade(row) for row in rows]


def update_user_trade(user: CurrentUser, trade_id: int, payload: TradeUpdateRequest) -> UserTakenTrade:
    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=422, detail="No trade updates provided.")
    fields = ", ".join(f"{key} = ?" for key in updates)
    values = [*updates.values(), trade_id, user.id]
    with get_connection() as connection:
        connection.execute(f"UPDATE user_taken_trades SET {fields} WHERE id = ? AND user_id = ?", values)
        row = connection.execute("SELECT * FROM user_taken_trades WHERE id = ? AND user_id = ?", (trade_id, user.id)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Trade not found.")
    return row_to_taken_trade(row)


def delete_user_trade(user: CurrentUser, trade_id: int) -> dict[str, bool]:
    with get_connection() as connection:
        cursor = connection.execute("DELETE FROM user_taken_trades WHERE id = ? AND user_id = ?", (trade_id, user.id))
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="Trade not found.")
    return {"deleted": True}


def record_webhook(source: str, event: str, payload: dict) -> dict[str, Any]:
    with get_connection() as connection:
        cursor = connection.execute(
            "INSERT INTO webhook_events (source, event, payload_json) VALUES (?, ?, ?)",
            (source, event, json.dumps(payload)),
        )
    return {"accepted": True, "event_id": int(cursor.lastrowid)}


def record_execution_log(status: str, payload: dict, reason: str | None = None) -> dict[str, Any]:
    with get_connection() as connection:
        cursor = connection.execute(
            "INSERT INTO execution_logs (signal_id, symbol, status, reason, payload_json) VALUES (?, ?, ?, ?, ?)",
            (payload.get("signal_id"), payload.get("symbol"), status, reason, json.dumps(payload)),
        )
    return {"logged": True, "id": int(cursor.lastrowid), "status": status}
