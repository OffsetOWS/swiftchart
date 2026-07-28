from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.ea.models import EAExecutionState, EAHeartbeatRequest, EAPendingSignal, EATradeUpdateRequest
from app.mt5.models import ForexAutoSignal, ValidationResult
from app.utils.database import get_connection


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _dt(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


def upsert_client(api_key_hash: str, heartbeat: EAHeartbeatRequest | None = None) -> None:
    payload = heartbeat.model_dump(mode="json") if heartbeat else {}
    client_id = heartbeat.client_id if heartbeat else "default"
    terminal_id = heartbeat.terminal_id if heartbeat else None
    ea_version = heartbeat.ea_version if heartbeat else None
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO ea_clients (client_id, api_key_hash, terminal_id, ea_version, metadata_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT(api_key_hash) DO UPDATE SET
                client_id=excluded.client_id,
                terminal_id=excluded.terminal_id,
                ea_version=excluded.ea_version,
                metadata_json=excluded.metadata_json,
                updated_at=CURRENT_TIMESTAMP
            """,
            (client_id, api_key_hash, terminal_id, ea_version, json.dumps(payload)),
        )


def save_heartbeat(api_key_hash: str, heartbeat: EAHeartbeatRequest) -> None:
    upsert_client(api_key_hash, heartbeat)
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO ea_heartbeats (
                client_id, api_key_hash, terminal_id, ea_version, broker_name, account_currency,
                balance, equity, margin_free, trading_allowed, open_positions, last_error, metadata_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                heartbeat.client_id,
                api_key_hash,
                heartbeat.terminal_id,
                heartbeat.ea_version,
                heartbeat.broker_name,
                heartbeat.account_currency,
                heartbeat.balance,
                heartbeat.equity,
                heartbeat.margin_free,
                int(heartbeat.trading_allowed),
                heartbeat.open_positions,
                heartbeat.last_error,
                json.dumps(heartbeat.metadata),
                _dt(_now()),
            ),
        )


def pending_signal_exists(trade_id: str) -> bool:
    with get_connection() as connection:
        row = connection.execute("SELECT 1 FROM ea_pending_signals WHERE trade_id = ? LIMIT 1", (trade_id,)).fetchone()
    return bool(row)


def queue_signal(signal: ForexAutoSignal, validation: ValidationResult, metadata: dict[str, Any] | None = None) -> EAPendingSignal:
    now = _now()
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO ea_pending_signals (
                trade_id, signal_json, validation_json, status, metadata_json, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(trade_id) DO UPDATE SET
                validation_json=excluded.validation_json,
                metadata_json=excluded.metadata_json,
                updated_at=excluded.updated_at
            """,
            (
                signal.trade_id,
                signal.model_dump_json(),
                validation.model_dump_json(),
                EAExecutionState.received.value,
                json.dumps(metadata or {}),
                _dt(now),
                _dt(now),
            ),
        )
    return get_pending_signal(signal.trade_id)  # type: ignore[return-value]


def get_pending_signal(trade_id: str) -> EAPendingSignal | None:
    with get_connection() as connection:
        row = connection.execute("SELECT * FROM ea_pending_signals WHERE trade_id = ?", (trade_id,)).fetchone()
    return _row_to_pending(row) if row else None


def fetch_pending_signals(limit: int = 20) -> list[EAPendingSignal]:
    now = _dt(_now())
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT * FROM ea_pending_signals
            WHERE status = ?
            ORDER BY created_at ASC
            LIMIT ?
            """,
            (EAExecutionState.received.value, limit),
        ).fetchall()
        trade_ids = [row["trade_id"] for row in rows]
        for trade_id in trade_ids:
            connection.execute(
                "UPDATE ea_pending_signals SET status = ?, fetched_at = COALESCE(fetched_at, ?), updated_at = ? WHERE trade_id = ?",
                (EAExecutionState.executing.value, now, now, trade_id),
            )
            _insert_update(
                connection,
                EATradeUpdateRequest(trade_id=trade_id, status=EAExecutionState.executing, message="EA fetched signal."),
                api_key_hash=None,
            )
    return [get_pending_signal(trade_id) for trade_id in trade_ids if get_pending_signal(trade_id)]


def save_trade_update(update: EATradeUpdateRequest, api_key_hash: str | None) -> EAPendingSignal | None:
    now = _dt(_now())
    with get_connection() as connection:
        _insert_update(connection, update, api_key_hash)
        connection.execute(
            """
            UPDATE ea_pending_signals
            SET status = ?, broker_order_id = COALESCE(?, broker_order_id),
                broker_position_id = COALESCE(?, broker_position_id),
                executed_price = COALESCE(?, executed_price),
                executed_volume = COALESCE(?, executed_volume),
                pnl = COALESCE(?, pnl),
                metadata_json = ?,
                updated_at = ?
            WHERE trade_id = ?
            """,
            (
                update.status.value,
                update.broker_order_id,
                update.broker_position_id,
                update.executed_price,
                update.executed_volume,
                update.pnl,
                json.dumps(update.metadata),
                now,
                update.trade_id,
            ),
        )
    return get_pending_signal(update.trade_id)


def list_signals(status: str | None = None, limit: int = 100) -> list[EAPendingSignal]:
    with get_connection() as connection:
        if status:
            rows = connection.execute(
                "SELECT * FROM ea_pending_signals WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = connection.execute("SELECT * FROM ea_pending_signals ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    return [_row_to_pending(row) for row in rows]


def execution_counts() -> dict[str, int]:
    with get_connection() as connection:
        rows = connection.execute("SELECT status, COUNT(*) AS count FROM ea_pending_signals GROUP BY status").fetchall()
    return {row["status"]: int(row["count"]) for row in rows}


def today_trade_count() -> int:
    with get_connection() as connection:
        row = connection.execute(
            "SELECT COUNT(*) AS count FROM ea_pending_signals WHERE date(created_at) = date('now')"
        ).fetchone()
    return int(row["count"] or 0)


def open_trade_count(pair: str | None = None) -> int:
    active = (
        EAExecutionState.received.value,
        EAExecutionState.executing.value,
        EAExecutionState.executed.value,
        EAExecutionState.partially_closed.value,
        EAExecutionState.breakeven_moved.value,
        EAExecutionState.trailing_updated.value,
    )
    query = f"SELECT COUNT(*) AS count FROM ea_pending_signals WHERE status IN ({','.join('?' for _ in active)})"
    params: list[Any] = list(active)
    if pair:
        query += " AND json_extract(signal_json, '$.pair') = ?"
        params.append(pair)
    with get_connection() as connection:
        row = connection.execute(query, params).fetchone()
    return int(row["count"] or 0)


def _insert_update(connection: Any, update: EATradeUpdateRequest, api_key_hash: str | None) -> None:
    connection.execute(
        """
        INSERT INTO ea_trade_updates (
            trade_id, api_key_hash, status, message, broker_order_id, broker_position_id,
            executed_price, executed_volume, stop_loss, take_profit, pnl, metadata_json, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            update.trade_id,
            api_key_hash,
            update.status.value,
            update.message,
            update.broker_order_id,
            update.broker_position_id,
            update.executed_price,
            update.executed_volume,
            update.stop_loss,
            update.take_profit,
            update.pnl,
            json.dumps(update.metadata),
            _dt(_now()),
        ),
    )


def _row_to_pending(row: Any) -> EAPendingSignal:
    return EAPendingSignal(
        trade_id=row["trade_id"],
        signal=ForexAutoSignal.model_validate_json(row["signal_json"]),
        validation=ValidationResult.model_validate_json(row["validation_json"]),
        status=EAExecutionState(row["status"]),
        created_at=_parse_dt(row["created_at"]) or _now(),
        updated_at=_parse_dt(row["updated_at"]) or _now(),
        fetched_at=_parse_dt(row["fetched_at"]),
        metadata=json.loads(row["metadata_json"] or "{}"),
    )

