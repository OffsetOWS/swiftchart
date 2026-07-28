from __future__ import annotations

import json
from datetime import UTC, date, datetime
from typing import Any

from app.mt5.models import (
    ForexAutoSignal,
    MT5AccountSnapshot,
    PerformanceSnapshot,
    TradeEvent,
    TradeRecord,
    TradeStatus,
)
from app.utils.database import get_connection


def _dt(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


def save_account_snapshot(snapshot: MT5AccountSnapshot) -> None:
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO mt5_accounts (login, server, currency, balance, equity, margin_free, leverage, trade_allowed, connected, name, company, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(login, server) DO UPDATE SET
                currency=excluded.currency,
                balance=excluded.balance,
                equity=excluded.equity,
                margin_free=excluded.margin_free,
                leverage=excluded.leverage,
                trade_allowed=excluded.trade_allowed,
                connected=excluded.connected,
                name=excluded.name,
                company=excluded.company,
                updated_at=CURRENT_TIMESTAMP
            """,
            (
                snapshot.login,
                snapshot.server,
                snapshot.currency,
                snapshot.balance,
                snapshot.equity,
                snapshot.margin_free,
                snapshot.leverage,
                int(snapshot.trade_allowed),
                int(snapshot.connected),
                snapshot.name,
                snapshot.company,
            ),
        )


def latest_account_snapshot() -> MT5AccountSnapshot | None:
    with get_connection() as connection:
        row = connection.execute("SELECT * FROM mt5_accounts ORDER BY updated_at DESC LIMIT 1").fetchone()
    if not row:
        return None
    return MT5AccountSnapshot(
        login=row["login"],
        server=row["server"],
        currency=row["currency"],
        balance=row["balance"],
        equity=row["equity"],
        margin_free=row["margin_free"],
        leverage=row["leverage"],
        trade_allowed=bool(row["trade_allowed"]),
        connected=bool(row["connected"]),
        name=row["name"],
        company=row["company"],
    )


def create_trade_record(signal: ForexAutoSignal, lot_size: float, risk_percent: float, status: TradeStatus = TradeStatus.pending) -> TradeRecord:
    record = TradeRecord(
        trade_id=signal.trade_id,
        pair=signal.pair,
        side=signal.side,
        timeframe=signal.timeframe,
        entry=signal.entry,
        stop_loss=signal.stop_loss,
        tp1=signal.tp1,
        tp2=signal.tp2,
        confidence=signal.confidence,
        risk_percent=risk_percent,
        lot_size=lot_size,
        status=status,
        metadata={"signal_timestamp": signal.timestamp.isoformat(), "setup_score": signal.setup_score},
    )
    upsert_trade(record)
    return record


def upsert_trade(record: TradeRecord) -> None:
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO mt5_trades (
                trade_id, pair, side, timeframe, entry, stop_loss, tp1, tp2, confidence, risk_percent,
                lot_size, status, mt5_order_id, mt5_position_id, opened_at, closed_at, close_reason, pnl, metadata_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(trade_id) DO UPDATE SET
                stop_loss=excluded.stop_loss,
                tp1=excluded.tp1,
                tp2=excluded.tp2,
                lot_size=excluded.lot_size,
                status=excluded.status,
                mt5_order_id=excluded.mt5_order_id,
                mt5_position_id=excluded.mt5_position_id,
                opened_at=COALESCE(excluded.opened_at, mt5_trades.opened_at),
                closed_at=excluded.closed_at,
                close_reason=excluded.close_reason,
                pnl=excluded.pnl,
                metadata_json=excluded.metadata_json,
                updated_at=CURRENT_TIMESTAMP
            """,
            (
                record.trade_id,
                record.pair,
                record.side.value,
                record.timeframe,
                record.entry,
                record.stop_loss,
                record.tp1,
                record.tp2,
                record.confidence,
                record.risk_percent,
                record.lot_size,
                record.status.value,
                record.mt5_order_id,
                record.mt5_position_id,
                _dt(record.opened_at),
                _dt(record.closed_at),
                record.close_reason,
                record.pnl,
                json.dumps(record.metadata),
            ),
        )


def get_trade(trade_id: str) -> TradeRecord | None:
    with get_connection() as connection:
        row = connection.execute("SELECT * FROM mt5_trades WHERE trade_id = ?", (trade_id,)).fetchone()
    return _row_to_trade(row) if row else None


def list_trades(status: str | None = None, limit: int = 100) -> list[TradeRecord]:
    with get_connection() as connection:
        if status:
            rows = connection.execute(
                "SELECT * FROM mt5_trades WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                (status.upper(), limit),
            ).fetchall()
        else:
            rows = connection.execute("SELECT * FROM mt5_trades ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    return [_row_to_trade(row) for row in rows]


def open_trade_count(pair: str | None = None) -> int:
    query = "SELECT COUNT(*) AS count FROM mt5_trades WHERE status IN ('OPEN', 'PARTIALLY_CLOSED', 'PENDING')"
    params: tuple[Any, ...] = ()
    if pair:
        query += " AND pair = ?"
        params = (pair,)
    with get_connection() as connection:
        return int(connection.execute(query, params).fetchone()["count"])


def current_total_lots() -> float:
    with get_connection() as connection:
        row = connection.execute(
            "SELECT COALESCE(SUM(lot_size), 0) AS total FROM mt5_trades WHERE status IN ('OPEN', 'PARTIALLY_CLOSED', 'PENDING')"
        ).fetchone()
    return float(row["total"])


def today_trade_count() -> int:
    today = date.today().isoformat()
    with get_connection() as connection:
        row = connection.execute(
            "SELECT COUNT(*) AS count FROM mt5_trades WHERE date(created_at) = date(?)",
            (today,),
        ).fetchone()
    return int(row["count"])


def today_pnl() -> float:
    today = date.today().isoformat()
    with get_connection() as connection:
        row = connection.execute(
            "SELECT COALESCE(SUM(pnl), 0) AS pnl FROM mt5_trades WHERE date(COALESCE(closed_at, created_at)) = date(?)",
            (today,),
        ).fetchone()
    return float(row["pnl"])


def duplicate_signal_exists(trade_id: str) -> bool:
    with get_connection() as connection:
        row = connection.execute("SELECT 1 FROM mt5_trades WHERE trade_id = ? LIMIT 1", (trade_id,)).fetchone()
    return bool(row)


def add_trade_event(event: TradeEvent) -> None:
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO mt5_trade_events (trade_id, event_type, message, metadata_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (event.trade_id, event.event_type, event.message, json.dumps(event.metadata), _dt(event.created_at)),
        )


def performance_snapshot() -> PerformanceSnapshot:
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN status IN ('OPEN', 'PARTIALLY_CLOSED', 'PENDING') THEN 1 ELSE 0 END) AS open_count,
                SUM(CASE WHEN status = 'CLOSED' THEN 1 ELSE 0 END) AS closed_count,
                SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) AS wins,
                SUM(CASE WHEN pnl < 0 THEN 1 ELSE 0 END) AS losses,
                COALESCE(SUM(pnl), 0) AS pnl
            FROM mt5_trades
            """
        ).fetchone()
    total = int(row["total"] or 0)
    wins = int(row["wins"] or 0)
    losses = int(row["losses"] or 0)
    decided = wins + losses
    return PerformanceSnapshot(
        total_trades=total,
        open_trades=int(row["open_count"] or 0),
        closed_trades=int(row["closed_count"] or 0),
        wins=wins,
        losses=losses,
        win_rate=(wins / decided) * 100 if decided else 0,
        realized_pnl=float(row["pnl"] or 0),
        today_pnl=today_pnl(),
        today_trade_count=today_trade_count(),
    )


def _row_to_trade(row: Any) -> TradeRecord:
    return TradeRecord(
        trade_id=row["trade_id"],
        pair=row["pair"],
        side=row["side"],
        timeframe=row["timeframe"],
        entry=row["entry"],
        stop_loss=row["stop_loss"],
        tp1=row["tp1"],
        tp2=row["tp2"],
        confidence=row["confidence"],
        risk_percent=row["risk_percent"],
        lot_size=row["lot_size"],
        status=row["status"],
        mt5_order_id=row["mt5_order_id"],
        mt5_position_id=row["mt5_position_id"],
        opened_at=_parse_dt(row["opened_at"]),
        closed_at=_parse_dt(row["closed_at"]),
        close_reason=row["close_reason"],
        pnl=row["pnl"],
        metadata=json.loads(row["metadata_json"] or "{}"),
    )
