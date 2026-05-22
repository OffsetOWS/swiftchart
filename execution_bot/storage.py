from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from execution_bot.config import get_execution_settings
from execution_bot.models import BotStatus, ExecutionPlan, SignalDecision, SignalIn

_INITIALIZED = False


def _sqlite_path() -> Path:
    url = get_execution_settings().execution_database_url
    if not url.startswith("sqlite:///"):
        raise ValueError("Execution bot currently supports SQLite EXECUTION_DATABASE_URL values.")
    return Path(url.replace("sqlite:///", "", 1))


def _connect():
    path = _sqlite_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


@contextmanager
def get_connection():
    ensure_db()
    connection = _connect()
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


def ensure_db() -> None:
    global _INITIALIZED
    if _INITIALIZED:
        return
    init_db()


def init_db() -> None:
    global _INITIALIZED
    with _connect() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS execution_signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_hash TEXT NOT NULL UNIQUE,
                signal_id TEXT,
                pair TEXT NOT NULL,
                side TEXT NOT NULL,
                entry REAL NOT NULL,
                confidence REAL NOT NULL,
                timeframe TEXT NOT NULL,
                exchange TEXT NOT NULL,
                reason TEXT,
                accepted INTEGER NOT NULL,
                rejection_reason TEXT,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS execution_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_hash TEXT NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                entry REAL NOT NULL,
                stop_loss REAL NOT NULL,
                position_size REAL NOT NULL,
                leverage REAL NOT NULL,
                risk_amount REAL NOT NULL,
                risk_percent REAL NOT NULL,
                take_profits TEXT NOT NULL,
                mode TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                exchange_order_id TEXT,
                final_pnl REAL,
                balance_after REAL,
                notes TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                closed_at TEXT
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS execution_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_id INTEGER,
                event_type TEXT NOT NULL,
                details TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS execution_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS execution_webhook_nonces (
                nonce TEXT PRIMARY KEY,
                timestamp INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute("INSERT OR IGNORE INTO execution_state (key, value) VALUES ('status', 'active')")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_execution_trades_status ON execution_trades(status)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_execution_signals_created_at ON execution_signals(created_at)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_execution_events_created_at ON execution_events(created_at)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_execution_webhook_nonces_created_at ON execution_webhook_nonces(created_at)")
        _ensure_trade_columns(connection)
    _INITIALIZED = True


def _ensure_trade_columns(connection: sqlite3.Connection) -> None:
    columns = {row["name"] for row in connection.execute("PRAGMA table_info(execution_trades)").fetchall()}
    additions = {
        "fill_price": "REAL",
        "filled_at": "TEXT",
        "stop_order_id": "TEXT",
        "tp_order_ids": "TEXT",
        "last_exchange_sync_at": "TEXT",
        "exchange_payload": "TEXT",
    }
    for column, definition in additions.items():
        if column not in columns:
            connection.execute(f"ALTER TABLE execution_trades ADD COLUMN {column} {definition}")


def signal_hash(signal: SignalIn) -> str:
    if signal.signal_id:
        return signal.signal_id
    rounded_entry = round(signal.entry, 6)
    return f"{signal.pair}:{signal.side.value}:{rounded_entry}:{signal.timeframe}:{signal.created_at.isoformat()[:16]}"


def get_status() -> BotStatus:
    with get_connection() as connection:
        row = connection.execute("SELECT value FROM execution_state WHERE key = 'status'").fetchone()
    return BotStatus(row["value"] if row else "active")


def set_status(status: BotStatus) -> None:
    with get_connection() as connection:
        connection.execute("INSERT OR REPLACE INTO execution_state (key, value) VALUES ('status', ?)", (status.value,))


def state_value(key: str, default: str | None = None) -> str | None:
    with get_connection() as connection:
        row = connection.execute("SELECT value FROM execution_state WHERE key = ?", (key,)).fetchone()
    return str(row["value"]) if row else default


def set_state_value(key: str, value: str) -> None:
    with get_connection() as connection:
        connection.execute("INSERT OR REPLACE INTO execution_state (key, value) VALUES (?, ?)", (key, value))


def runtime_base_risk_percent() -> float:
    settings = get_execution_settings()
    value = state_value("base_risk_percent_override")
    if value is None:
        return settings.base_risk_percent
    try:
        return max(0.1, min(settings.max_risk_percent, float(value)))
    except ValueError:
        return settings.base_risk_percent


def account_balance() -> float:
    settings = get_execution_settings()
    stored_balance = state_value("account_balance")
    if stored_balance is not None:
        try:
            return float(stored_balance)
        except ValueError:
            pass
    with get_connection() as connection:
        row = connection.execute("SELECT balance_after FROM execution_trades WHERE balance_after IS NOT NULL ORDER BY id DESC LIMIT 1").fetchone()
    return float(row["balance_after"]) if row else settings.starting_balance


def set_account_balance(balance: float) -> None:
    if balance <= 0:
        return
    set_state_value("account_balance", str(balance))
    set_state_value("account_balance_synced_at", datetime.now(timezone.utc).isoformat())


def open_trade_count() -> int:
    with get_connection() as connection:
        row = connection.execute("SELECT COUNT(*) AS count FROM execution_trades WHERE status = 'open'").fetchone()
    return int(row["count"])


def open_exposure_for_symbol(symbol: str) -> float:
    with get_connection() as connection:
        row = connection.execute("SELECT COALESCE(SUM(position_size * entry), 0) AS exposure FROM execution_trades WHERE status = 'open' AND symbol = ?", (symbol,)).fetchone()
    return float(row["exposure"])


def consecutive_losses() -> int:
    with get_connection() as connection:
        rows = connection.execute("SELECT final_pnl FROM execution_trades WHERE status = 'closed' AND final_pnl IS NOT NULL ORDER BY id DESC LIMIT 5").fetchall()
    losses = 0
    for row in rows:
        if float(row["final_pnl"]) < 0:
            losses += 1
        else:
            break
    return losses


def daily_pnl() -> float:
    since = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    with get_connection() as connection:
        row = connection.execute("SELECT COALESCE(SUM(final_pnl), 0) AS pnl FROM execution_trades WHERE closed_at >= ?", (since,)).fetchone()
    return float(row["pnl"])


def weekly_pnl() -> float:
    since = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    with get_connection() as connection:
        row = connection.execute("SELECT COALESCE(SUM(final_pnl), 0) AS pnl FROM execution_trades WHERE closed_at >= ?", (since,)).fetchone()
    return float(row["pnl"])


def is_duplicate(signal: SignalIn, duplicate_window_seconds: int) -> bool:
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=duplicate_window_seconds)).isoformat()
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT id FROM execution_signals
            WHERE pair = ? AND side = ? AND timeframe = ? AND ABS(entry - ?) < 0.000001 AND created_at >= ? AND accepted = 1
            LIMIT 1
            """,
            (signal.pair, signal.side.value, signal.timeframe, signal.entry, cutoff),
        ).fetchone()
    return row is not None


def claim_signal(signal: SignalIn) -> bool:
    key = signal_hash(signal)
    try:
        with get_connection() as connection:
            connection.execute(
                """
                INSERT INTO execution_signals
                (signal_hash, signal_id, pair, side, entry, confidence, timeframe, exchange, reason, accepted, rejection_reason, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 'processing', ?)
                """,
                (
                    key,
                    signal.signal_id,
                    signal.pair,
                    signal.side.value,
                    signal.entry,
                    signal.confidence,
                    signal.timeframe,
                    signal.exchange,
                    signal.reason,
                    signal.model_dump_json(),
                ),
            )
    except sqlite3.IntegrityError:
        return False
    return True


def record_signal(decision: SignalDecision) -> str:
    key = signal_hash(decision.signal)
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO execution_signals
            (signal_hash, signal_id, pair, side, entry, confidence, timeframe, exchange, reason, accepted, rejection_reason, payload)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(signal_hash) DO UPDATE SET
              accepted = CASE
                WHEN execution_signals.accepted = 1 AND excluded.accepted = 0 THEN execution_signals.accepted
                ELSE excluded.accepted
              END,
              rejection_reason = CASE
                WHEN execution_signals.accepted = 1 AND excluded.accepted = 0 THEN execution_signals.rejection_reason
                ELSE excluded.rejection_reason
              END,
              payload = excluded.payload
            """,
            (
                key,
                decision.signal.signal_id,
                decision.signal.pair,
                decision.signal.side.value,
                decision.signal.entry,
                decision.signal.confidence,
                decision.signal.timeframe,
                decision.signal.exchange,
                decision.signal.reason,
                1 if decision.accepted else 0,
                None if decision.accepted else decision.reason,
                decision.signal.model_dump_json(),
            ),
        )
    return key


def log_event(event_type: str, details: dict[str, Any] | str | None = None, trade_id: int | None = None) -> None:
    if isinstance(details, str) or details is None:
        payload = details
    else:
        payload = json.dumps(details)
    with get_connection() as connection:
        connection.execute(
            "INSERT INTO execution_events (trade_id, event_type, details) VALUES (?, ?, ?)",
            (trade_id, event_type, payload),
        )


def claim_webhook_nonce(nonce: str, timestamp: int, ttl_seconds: int) -> bool:
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=ttl_seconds)).strftime("%Y-%m-%d %H:%M:%S")
    try:
        with get_connection() as connection:
            connection.execute("DELETE FROM execution_webhook_nonces WHERE created_at < ?", (cutoff,))
            connection.execute(
                "INSERT INTO execution_webhook_nonces (nonce, timestamp) VALUES (?, ?)",
                (nonce, int(timestamp)),
            )
    except sqlite3.IntegrityError:
        return False
    return True


def recent_event_count(event_type: str, window_seconds: int) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=window_seconds)).isoformat()
    with get_connection() as connection:
        row = connection.execute(
            "SELECT COUNT(*) AS count FROM execution_events WHERE event_type = ? AND created_at >= ?",
            (event_type, cutoff),
        ).fetchone()
    return int(row["count"] or 0)


def record_trade(signal_key: str, plan: ExecutionPlan, order_id: str | None = None) -> int:
    with get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO execution_trades
            (signal_hash, symbol, side, entry, stop_loss, position_size, leverage, risk_amount, risk_percent, take_profits, mode, exchange_order_id, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                signal_key,
                plan.symbol,
                plan.side.value,
                plan.entry,
                plan.stop_loss,
                plan.position_size,
                plan.leverage,
                plan.risk_amount,
                plan.risk_percent,
                json.dumps(plan.take_profits),
                plan.mode.value,
                order_id,
                "; ".join(plan.notes),
            ),
        )
        trade_id = int(cursor.lastrowid)
        connection.execute("INSERT INTO execution_events (trade_id, event_type, details) VALUES (?, 'entry', ?)", (trade_id, plan.model_dump_json()))
    return trade_id


def update_trade_execution_details(trade_id: int, order: dict[str, Any], balance: float | None = None) -> None:
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE execution_trades
            SET fill_price = ?,
                filled_at = ?,
                stop_order_id = ?,
                tp_order_ids = ?,
                balance_after = COALESCE(?, balance_after),
                last_exchange_sync_at = ?,
                exchange_payload = ?
            WHERE id = ?
            """,
            (
                order.get("fill_price"),
                order.get("filled_at"),
                str(order.get("stop_order_id")) if order.get("stop_order_id") is not None else None,
                json.dumps(order.get("tp_order_ids", [])),
                balance,
                datetime.now(timezone.utc).isoformat(),
                json.dumps(order),
                trade_id,
            ),
        )
    if balance is not None:
        set_account_balance(float(balance))


def close_trade(trade_id: int, final_pnl: float, balance: float | None = None, notes: str | None = None) -> None:
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE execution_trades
            SET status = 'closed',
                final_pnl = ?,
                balance_after = COALESCE(?, balance_after),
                notes = COALESCE(?, notes),
                closed_at = ?
            WHERE id = ? AND status = 'open'
            """,
            (final_pnl, balance, notes, datetime.now(timezone.utc).isoformat(), trade_id),
        )
    if balance is not None:
        set_account_balance(float(balance))


def list_open_trades(limit: int = 10) -> list[dict[str, Any]]:
    with get_connection() as connection:
        return [
            dict(row)
            for row in connection.execute(
                "SELECT * FROM execution_trades WHERE status = 'open' ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        ]


def list_closed_trades(limit: int = 10) -> list[dict[str, Any]]:
    with get_connection() as connection:
        return [
            dict(row)
            for row in connection.execute(
                "SELECT * FROM execution_trades WHERE status = 'closed' ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        ]


def trade_totals() -> dict[str, float | int]:
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT
              COUNT(*) AS total,
              SUM(CASE WHEN final_pnl > 0 THEN 1 ELSE 0 END) AS wins,
              COALESCE(SUM(final_pnl), 0) AS pnl
            FROM execution_trades
            WHERE status = 'closed'
            """
        ).fetchone()
    total = int(row["total"] or 0)
    wins = int(row["wins"] or 0)
    return {
        "total": total,
        "wins": wins,
        "win_rate": (wins / total * 100) if total else 0,
        "pnl": float(row["pnl"] or 0),
    }


def dashboard() -> dict[str, Any]:
    balance = account_balance()
    with get_connection() as connection:
        open_trades = [dict(row) for row in connection.execute("SELECT * FROM execution_trades WHERE status = 'open' ORDER BY id DESC").fetchall()]
        closed_trades = [dict(row) for row in connection.execute("SELECT * FROM execution_trades WHERE status = 'closed' ORDER BY id DESC LIMIT 50").fetchall()]
        totals = connection.execute(
            """
            SELECT
              COUNT(*) AS total,
              SUM(CASE WHEN final_pnl > 0 THEN 1 ELSE 0 END) AS wins,
              COALESCE(SUM(final_pnl), 0) AS pnl
            FROM execution_trades
            WHERE status = 'closed'
            """
        ).fetchone()
        open_risk_row = connection.execute("SELECT COALESCE(SUM(risk_amount), 0) AS risk FROM execution_trades WHERE status = 'open'").fetchone()
    total = int(totals["total"] or 0)
    wins = int(totals["wins"] or 0)
    return {
        "balance": balance,
        "active_trades": open_trades,
        "closed_trades": closed_trades,
        "win_rate": (wins / total * 100) if total else 0,
        "profit_loss": float(totals["pnl"] or 0),
        "daily_pnl": daily_pnl(),
        "weekly_pnl": weekly_pnl(),
        "daily_drawdown": min(0, daily_pnl()),
        "open_risk": float(open_risk_row["risk"] or 0),
        "mode": "live" if get_execution_settings().live_enabled else "paper",
        "status": get_status().value,
        "base_risk_percent": runtime_base_risk_percent(),
        "total_trades": total,
    }
