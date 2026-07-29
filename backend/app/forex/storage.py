from __future__ import annotations

from datetime import UTC, datetime
import sqlite3
from typing import Any
from uuid import uuid4

from app.forex.models import ACTIVE_FOREX_STATUSES, ForexSignalPlan
from app.utils.database import get_connection


FOREX_COLUMNS: dict[str, str] = {
    "public_id": "TEXT",
    "symbol": "TEXT",
    "entry_type": "TEXT NOT NULL DEFAULT 'ZONE'",
    "entry_price": "REAL",
    "entry_low": "REAL",
    "entry_high": "REAL",
    "take_profit_1": "REAL",
    "take_profit_2": "REAL",
    "risk_reward_1": "REAL",
    "risk_reward_2": "REAL",
    "execution_timeframe": "TEXT",
    "setup_timeframe": "TEXT",
    "bias_timeframe": "TEXT",
    "timeframe_alignment": "TEXT",
    "htf_bias": "TEXT",
    "setup_structure": "TEXT",
    "entry_trigger": "TEXT",
    "market_session": "TEXT",
    "setup_score": "REAL",
    "strategy_family": "TEXT",
    "strategy_version": "TEXT",
    "market_regime": "TEXT",
    "bias": "TEXT",
    "setup_reason": "TEXT",
    "activated_at": "TEXT",
    "expires_at": "TEXT",
    "closed_at": "TEXT",
    "telegram_dispatched_at": "TEXT",
    "source_scan_id": "TEXT",
    "dedupe_key": "TEXT",
    "last_market_price": "REAL",
    "last_price_updated_at": "TEXT",
    "is_legacy": "INTEGER NOT NULL DEFAULT 0",
}


def ensure_forex_schema() -> None:
    with get_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS forex_signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                market_type TEXT NOT NULL DEFAULT 'forex',
                pair TEXT NOT NULL,
                direction TEXT NOT NULL,
                score REAL NOT NULL DEFAULT 0,
                grade TEXT,
                session TEXT,
                entry REAL,
                stop_loss REAL,
                tp1 REAL,
                tp2 REAL,
                rr REAL,
                news_risk TEXT,
                spread_status TEXT,
                reason TEXT,
                status TEXT NOT NULL DEFAULT 'PENDING_ENTRY',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        existing = {row["name"] for row in connection.execute("PRAGMA table_info(forex_signals)")}
        for column, definition in FOREX_COLUMNS.items():
            if column not in existing:
                connection.execute(f"ALTER TABLE forex_signals ADD COLUMN {column} {definition}")
        connection.execute(
            """
            UPDATE forex_signals
            SET
                public_id = COALESCE(public_id, 'legacy-' || id),
                symbol = COALESCE(symbol, pair),
                entry_price = COALESCE(entry_price, entry),
                entry_low = COALESCE(entry_low, entry),
                entry_high = COALESCE(entry_high, entry),
                take_profit_1 = COALESCE(take_profit_1, tp1),
                take_profit_2 = COALESCE(take_profit_2, tp2),
                risk_reward_2 = COALESCE(risk_reward_2, rr),
                setup_score = COALESCE(setup_score, score),
                market_session = COALESCE(market_session, session),
                setup_reason = COALESCE(setup_reason, reason),
                execution_timeframe = COALESCE(execution_timeframe, '15m'),
                setup_timeframe = COALESCE(setup_timeframe, '1h'),
                bias_timeframe = COALESCE(bias_timeframe, '4h'),
                strategy_family = COALESCE(strategy_family, 'legacy'),
                strategy_version = COALESCE(strategy_version, 'legacy'),
                is_legacy = CASE WHEN source_scan_id IS NULL THEN 1 ELSE is_legacy END,
                expires_at = COALESCE(expires_at, datetime(created_at, '+24 hours')),
                status = CASE
                    WHEN status IN ('active', 'wait') THEN 'EXPIRED'
                    ELSE status
                END
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS forex_scan_runs (
                id TEXT PRIMARY KEY,
                started_at TEXT NOT NULL,
                completed_at TEXT,
                provider TEXT NOT NULL,
                status TEXT NOT NULL,
                created_count INTEGER NOT NULL DEFAULT 0,
                reused_count INTEGER NOT NULL DEFAULT 0,
                error_message TEXT
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS forex_telegram_dispatches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_id TEXT NOT NULL,
                chat_id TEXT NOT NULL,
                queued_at TEXT NOT NULL,
                attempted_at TEXT,
                delivered_at TEXT,
                failed_at TEXT,
                retry_count INTEGER NOT NULL DEFAULT 0,
                error_message TEXT,
                UNIQUE(signal_id, chat_id)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS forex_trade_preparations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_id TEXT NOT NULL,
                user_id TEXT,
                account_balance REAL NOT NULL,
                risk_percentage REAL NOT NULL,
                risk_amount REAL NOT NULL,
                position_size REAL NOT NULL,
                execution_method TEXT NOT NULL,
                signal_snapshot TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_forex_signal_public_id ON forex_signals(public_id)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_forex_signal_status ON forex_signals(status, created_at)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_forex_signal_dedupe ON forex_signals(dedupe_key, status)")
        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_forex_signal_active_dedupe
            ON forex_signals(dedupe_key)
            WHERE dedupe_key IS NOT NULL
              AND status IN ('PENDING_ENTRY', 'OPEN', 'TP1_HIT')
            """
        )
        connection.execute("CREATE INDEX IF NOT EXISTS idx_forex_dispatch_pending ON forex_telegram_dispatches(delivered_at, retry_count)")


def start_scan_run(scan_id: str, provider: str, started_at: datetime) -> None:
    ensure_forex_schema()
    with get_connection() as connection:
        connection.execute(
            "INSERT INTO forex_scan_runs (id, started_at, provider, status) VALUES (?, ?, ?, 'RUNNING')",
            (scan_id, started_at.isoformat(), provider),
        )


def finish_scan_run(
    scan_id: str,
    *,
    created_count: int,
    reused_count: int,
    error_message: str | None = None,
) -> None:
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE forex_scan_runs
            SET completed_at = ?, status = ?, created_count = ?, reused_count = ?, error_message = ?
            WHERE id = ?
            """,
            (
                datetime.now(UTC).isoformat(),
                "FAILED" if error_message else "COMPLETED",
                created_count,
                reused_count,
                error_message,
                scan_id,
            ),
        )


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _row_to_signal(row: sqlite3.Row) -> ForexSignalPlan:
    created = _parse_datetime(row["created_at"]) or datetime.now(UTC)
    expires = _parse_datetime(row["expires_at"]) or created
    return ForexSignalPlan(
        id=row["public_id"],
        symbol=row["symbol"] or row["pair"],
        direction=row["direction"],
        entry_type=row["entry_type"] or "ZONE",
        entry_price=float(row["entry_price"] or 0),
        entry_low=float(row["entry_low"] or 0),
        entry_high=float(row["entry_high"] or 0),
        stop_loss=float(row["stop_loss"] or 0),
        take_profit_1=float(row["take_profit_1"] or 0),
        take_profit_2=float(row["take_profit_2"] or 0),
        risk_reward_1=float(row["risk_reward_1"] or 0),
        risk_reward_2=float(row["risk_reward_2"] or 0),
        execution_timeframe=row["execution_timeframe"] or "15m",
        setup_timeframe=row["setup_timeframe"] or "1h",
        bias_timeframe=row["bias_timeframe"] or "4h",
        timeframe_alignment=row["timeframe_alignment"] or "Legacy alignment unavailable",
        htf_bias=row["htf_bias"] or row["bias"] or "UNKNOWN",
        setup_structure=row["setup_structure"] or "Legacy structure unavailable",
        entry_trigger=row["entry_trigger"] or "Legacy trigger unavailable",
        market_session=row["market_session"] or row["session"] or "Unknown",
        setup_score=float(row["setup_score"] or row["score"] or 0),
        strategy_family=row["strategy_family"] or "legacy",
        strategy_version=row["strategy_version"] or "legacy",
        market_regime=row["market_regime"] or "Unknown",
        bias=row["bias"] or "Unknown",
        setup_reason=row["setup_reason"] or row["reason"] or "Legacy Forex signal.",
        status=row["status"],
        created_at=created,
        activated_at=_parse_datetime(row["activated_at"]),
        expires_at=expires,
        closed_at=_parse_datetime(row["closed_at"]),
        telegram_dispatched_at=_parse_datetime(row["telegram_dispatched_at"]),
        source_scan_id=row["source_scan_id"] or f"legacy-{row['id']}",
        dedupe_key=row["dedupe_key"] or f"legacy-{row['id']}",
        last_market_price=float(row["last_market_price"]) if row["last_market_price"] is not None else None,
        last_price_updated_at=_parse_datetime(row["last_price_updated_at"]),
        is_legacy=bool(row["is_legacy"]),
    )


def find_active_by_dedupe(dedupe_key: str) -> ForexSignalPlan | None:
    ensure_forex_schema()
    placeholders = ",".join("?" for _ in ACTIVE_FOREX_STATUSES)
    with get_connection() as connection:
        row = connection.execute(
            f"""
            SELECT * FROM forex_signals
            WHERE dedupe_key = ? AND status IN ({placeholders})
            ORDER BY created_at DESC LIMIT 1
            """,
            (dedupe_key, *ACTIVE_FOREX_STATUSES),
        ).fetchone()
    return _row_to_signal(row) if row else None


def has_recent_signal(symbol: str, strategy_family: str, since: datetime) -> bool:
    ensure_forex_schema()
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT 1 FROM forex_signals
            WHERE symbol = ? AND strategy_family = ? AND created_at >= ?
            LIMIT 1
            """,
            (symbol, strategy_family, since.isoformat()),
        ).fetchone()
    return bool(row)


def insert_signal(plan: dict[str, Any]) -> ForexSignalPlan:
    ensure_forex_schema()
    public_id = str(plan.get("id") or uuid4())
    values = {
        **plan,
        "id": public_id,
        "created_at": plan["created_at"].isoformat(),
        "expires_at": plan["expires_at"].isoformat(),
    }
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO forex_signals (
                public_id, market_type, pair, symbol, direction, score, grade, session,
                entry, entry_type, entry_price, entry_low, entry_high, stop_loss, tp1, tp2,
                take_profit_1, take_profit_2, rr, risk_reward_1, risk_reward_2,
                news_risk, spread_status, reason, status, created_at,
                execution_timeframe, setup_timeframe, bias_timeframe, timeframe_alignment,
                htf_bias, setup_structure, entry_trigger, market_session, setup_score,
                strategy_family, strategy_version, market_regime, bias, setup_reason,
                expires_at, source_scan_id, dedupe_key, is_legacy
            )
            VALUES (
                :id, 'forex', :symbol, :symbol, :direction, :setup_score, :grade, :market_session,
                :entry_price, :entry_type, :entry_price, :entry_low, :entry_high, :stop_loss,
                :take_profit_1, :take_profit_2, :take_profit_1, :take_profit_2, :risk_reward_2,
                :risk_reward_1, :risk_reward_2, :news_risk, :spread_status, :setup_reason,
                :status, :created_at, :execution_timeframe, :setup_timeframe, :bias_timeframe,
                :timeframe_alignment, :htf_bias, :setup_structure, :entry_trigger,
                :market_session, :setup_score, :strategy_family, :strategy_version,
                :market_regime, :bias, :setup_reason, :expires_at, :source_scan_id,
                :dedupe_key, 0
            )
            """,
            values,
        )
        row = connection.execute("SELECT * FROM forex_signals WHERE public_id = ?", (public_id,)).fetchone()
    return _row_to_signal(row)


def list_signals(statuses: tuple[str, ...] | None = None, limit: int = 100) -> list[ForexSignalPlan]:
    ensure_forex_schema()
    query = "SELECT * FROM forex_signals"
    parameters: list[Any] = []
    if statuses:
        query += f" WHERE status IN ({','.join('?' for _ in statuses)})"
        parameters.extend(statuses)
    query += " ORDER BY created_at DESC LIMIT ?"
    parameters.append(max(1, min(limit, 500)))
    with get_connection() as connection:
        rows = connection.execute(query, parameters).fetchall()
    return [_row_to_signal(row) for row in rows]


def get_signal(signal_id: str) -> ForexSignalPlan | None:
    ensure_forex_schema()
    with get_connection() as connection:
        row = connection.execute(
            "SELECT * FROM forex_signals WHERE public_id = ? OR CAST(id AS TEXT) = ? LIMIT 1",
            (signal_id, signal_id),
        ).fetchone()
    return _row_to_signal(row) if row else None


def update_signal_market_state(
    signal_id: str,
    *,
    status: str,
    price: float,
    checked_at: datetime,
    activated_at: datetime | None = None,
    closed_at: datetime | None = None,
) -> ForexSignalPlan:
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE forex_signals
            SET status = ?, last_market_price = ?, last_price_updated_at = ?,
                activated_at = COALESCE(activated_at, ?),
                closed_at = COALESCE(closed_at, ?)
            WHERE public_id = ?
            """,
            (
                status,
                price,
                checked_at.isoformat(),
                activated_at.isoformat() if activated_at else None,
                closed_at.isoformat() if closed_at else None,
                signal_id,
            ),
        )
    signal = get_signal(signal_id)
    if signal is None:
        raise LookupError(signal_id)
    return signal


def queue_dispatches(signal_id: str, chat_ids: list[str], queued_at: datetime | None = None) -> int:
    ensure_forex_schema()
    queued = (queued_at or datetime.now(UTC)).isoformat()
    inserted = 0
    with get_connection() as connection:
        for chat_id in chat_ids:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO forex_telegram_dispatches (signal_id, chat_id, queued_at)
                VALUES (?, ?, ?)
                """,
                (signal_id, str(chat_id), queued),
            )
            inserted += cursor.rowcount
    return inserted


def claim_pending_dispatches(limit: int = 50) -> list[dict[str, Any]]:
    ensure_forex_schema()
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT * FROM forex_telegram_dispatches
            WHERE delivered_at IS NULL AND retry_count < 5
            ORDER BY queued_at ASC LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def mark_dispatch_attempt(dispatch_id: int, *, delivered: bool, error_message: str | None = None) -> None:
    now = datetime.now(UTC).isoformat()
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE forex_telegram_dispatches
            SET attempted_at = ?, delivered_at = CASE WHEN ? THEN ? ELSE delivered_at END,
                failed_at = CASE WHEN ? THEN failed_at ELSE ? END,
                retry_count = retry_count + 1, error_message = ?
            WHERE id = ?
            """,
            (now, int(delivered), now, int(delivered), now, error_message, dispatch_id),
        )
        if delivered:
            row = connection.execute(
                "SELECT signal_id FROM forex_telegram_dispatches WHERE id = ?",
                (dispatch_id,),
            ).fetchone()
            connection.execute(
                "UPDATE forex_signals SET telegram_dispatched_at = COALESCE(telegram_dispatched_at, ?) WHERE public_id = ?",
                (now, row["signal_id"]),
            )


def save_trade_preparation(
    signal: ForexSignalPlan,
    *,
    user_id: str | None,
    account_balance: float,
    risk_percentage: float,
    risk_amount: float,
    position_size: float,
    execution_method: str,
) -> None:
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO forex_trade_preparations (
                signal_id, user_id, account_balance, risk_percentage, risk_amount,
                position_size, execution_method, signal_snapshot, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                signal.id,
                user_id,
                account_balance,
                risk_percentage,
                risk_amount,
                position_size,
                execution_method,
                signal.model_dump_json(),
                datetime.now(UTC).isoformat(),
            ),
        )
