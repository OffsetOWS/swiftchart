from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
import sqlite3
from typing import Any
from uuid import uuid4

from app.config import get_settings
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
    "timeframe": "TEXT",
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
    "latest_price": "REAL",
    "latest_price_at": "TEXT",
    "activated_entry_price": "REAL",
    "tp1_hit_at": "TEXT",
    "tp2_hit_at": "TEXT",
    "stopped_at": "TEXT",
    "last_market_price": "REAL",
    "last_price_updated_at": "TEXT",
    "is_legacy": "INTEGER NOT NULL DEFAULT 0",
}
_FOREX_SCHEMA_READY_FOR: str | None = None


def ensure_forex_schema() -> None:
    global _FOREX_SCHEMA_READY_FOR
    database_url = get_settings().database_url
    if _FOREX_SCHEMA_READY_FOR == database_url:
        return
    with get_connection() as connection:
        # API, scheduler, and bot processes may initialize together. Serialize
        # the one-time migration so another process cannot restore the
        # immutable-plan trigger while legacy rows are being backfilled.
        connection.execute("BEGIN IMMEDIATE")
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
        needs_backfill = connection.execute(
            """
            SELECT 1 FROM forex_signals
            WHERE public_id IS NULL OR symbol IS NULL OR entry_price IS NULL
               OR entry_low IS NULL OR entry_high IS NULL OR take_profit_1 IS NULL
               OR take_profit_2 IS NULL OR risk_reward_2 IS NULL
               OR setup_score IS NULL OR market_session IS NULL
               OR setup_reason IS NULL OR timeframe IS NULL
               OR execution_timeframe IS NULL OR setup_timeframe IS NULL
               OR bias_timeframe IS NULL OR strategy_family IS NULL
               OR strategy_version IS NULL OR expires_at IS NULL
               OR status IN ('active', 'wait')
            LIMIT 1
            """
        ).fetchone()
        if needs_backfill:
            # Backfills must run before the immutable-plan guard is restored.
            connection.execute("DROP TRIGGER IF EXISTS preserve_forex_signal_plan")
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
                timeframe = UPPER(COALESCE(timeframe, execution_timeframe, '15M')),
                execution_timeframe = COALESCE(execution_timeframe, '15m'),
                setup_timeframe = COALESCE(setup_timeframe, '1h'),
                bias_timeframe = COALESCE(bias_timeframe, '4h'),
                strategy_family = COALESCE(strategy_family, 'legacy'),
                strategy_version = COALESCE(strategy_version, 'legacy'),
                latest_price = COALESCE(latest_price, last_market_price),
                latest_price_at = COALESCE(latest_price_at, last_price_updated_at),
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
                timeframe TEXT,
                status TEXT NOT NULL,
                created_count INTEGER NOT NULL DEFAULT 0,
                reused_count INTEGER NOT NULL DEFAULT 0,
                error_message TEXT
            )
            """
        )
        scan_run_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(forex_scan_runs)")
        }
        if "timeframe" not in scan_run_columns:
            connection.execute("ALTER TABLE forex_scan_runs ADD COLUMN timeframe TEXT")
        scan_run_additions = {
            "trigger_source": "TEXT NOT NULL DEFAULT 'scheduled'",
            "pairs_evaluated": "INTEGER NOT NULL DEFAULT 0",
            "candidate_count": "INTEGER NOT NULL DEFAULT 0",
            "rejected_count": "INTEGER NOT NULL DEFAULT 0",
            "telegram_queued_count": "INTEGER NOT NULL DEFAULT 0",
            "result_status": "TEXT",
            "rejection_summary": "TEXT",
        }
        for column, definition in scan_run_additions.items():
            if column not in scan_run_columns:
                connection.execute(
                    f"ALTER TABLE forex_scan_runs ADD COLUMN {column} {definition}"
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
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS forex_candles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider TEXT NOT NULL,
                instrument TEXT NOT NULL,
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                candle_open_at TEXT NOT NULL,
                candle_close_at TEXT NOT NULL,
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                volume REAL NOT NULL DEFAULT 0,
                complete INTEGER NOT NULL DEFAULT 1,
                source_timestamp TEXT NOT NULL,
                fetched_at TEXT NOT NULL,
                UNIQUE(provider, instrument, timeframe, candle_open_at)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS forex_market_data_locks (
                lock_key TEXT PRIMARY KEY,
                owner TEXT NOT NULL,
                acquired_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS forex_candle_evaluations (
                evaluation_key TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                candle_open_at TEXT NOT NULL,
                strategy_family TEXT NOT NULL,
                strategy_version TEXT NOT NULL,
                decision TEXT NOT NULL,
                reason TEXT,
                signal_id TEXT,
                evaluated_at TEXT NOT NULL
            )
            """
        )
        connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_forex_signal_public_id ON forex_signals(public_id)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_forex_signal_status ON forex_signals(status, created_at)")
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_forex_signal_timeframe_status "
            "ON forex_signals(timeframe, status, created_at)"
        )
        connection.execute("CREATE INDEX IF NOT EXISTS idx_forex_signal_dedupe ON forex_signals(dedupe_key, status)")
        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_forex_signal_active_dedupe
            ON forex_signals(dedupe_key)
            WHERE dedupe_key IS NOT NULL
              AND status IN ('PENDING_ENTRY', 'OPEN', 'TP1_HIT')
            """
        )
        connection.execute(
            """
            CREATE TRIGGER IF NOT EXISTS validate_forex_signal_timeframe_insert
            BEFORE INSERT ON forex_signals
            WHEN NEW.timeframe NOT IN ('15M', '1H', '4H', '1D')
            BEGIN
                SELECT RAISE(ABORT, 'invalid forex signal timeframe');
            END
            """
        )
        connection.execute(
            """
            CREATE TRIGGER IF NOT EXISTS validate_forex_signal_timeframe_update
            BEFORE UPDATE OF timeframe ON forex_signals
            WHEN NEW.timeframe NOT IN ('15M', '1H', '4H', '1D')
            BEGIN
                SELECT RAISE(ABORT, 'invalid forex signal timeframe');
            END
            """
        )
        connection.execute(
            """
            CREATE TRIGGER IF NOT EXISTS preserve_forex_signal_plan
            BEFORE UPDATE OF timeframe, direction, entry_price, entry_low, entry_high,
                stop_loss, take_profit_1, take_profit_2, risk_reward_1, risk_reward_2,
                strategy_family, strategy_version, setup_score, market_regime
            ON forex_signals
            BEGIN
                SELECT RAISE(ABORT, 'persisted forex signal plans are immutable');
            END
            """
        )
        connection.execute("CREATE INDEX IF NOT EXISTS idx_forex_dispatch_pending ON forex_telegram_dispatches(delivered_at, retry_count)")
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_forex_candles_latest "
            "ON forex_candles(symbol, timeframe, candle_open_at DESC)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_forex_evaluations_candle "
            "ON forex_candle_evaluations(symbol, timeframe, candle_open_at)"
        )
    _FOREX_SCHEMA_READY_FOR = database_url


def upsert_forex_candles(rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    ensure_forex_schema()
    with get_connection() as connection:
        before = connection.total_changes
        connection.executemany(
            """
            INSERT INTO forex_candles (
                provider, instrument, symbol, timeframe, candle_open_at,
                candle_close_at, open, high, low, close, volume, complete,
                source_timestamp, fetched_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(provider, instrument, timeframe, candle_open_at) DO UPDATE SET
                candle_close_at=excluded.candle_close_at,
                open=excluded.open, high=excluded.high, low=excluded.low,
                close=excluded.close, volume=excluded.volume,
                complete=excluded.complete,
                source_timestamp=excluded.source_timestamp,
                fetched_at=excluded.fetched_at
            """,
            [
                (
                    row["provider"], row["instrument"], row["symbol"], row["timeframe"],
                    row["candle_open_at"], row["candle_close_at"], row["open"],
                    row["high"], row["low"], row["close"], row.get("volume", 0),
                    int(row.get("complete", True)), row["source_timestamp"],
                    row["fetched_at"],
                )
                for row in rows
            ],
        )
        return connection.total_changes - before


def list_forex_candles(
    symbol: str,
    timeframe: str,
    *,
    limit: int = 500,
) -> list[dict[str, Any]]:
    ensure_forex_schema()
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT * FROM (
                SELECT *,
                    ROW_NUMBER() OVER (
                        PARTITION BY symbol, timeframe, candle_open_at
                        ORDER BY fetched_at DESC, id DESC
                    ) AS canonical_rank
                FROM forex_candles
                WHERE symbol = ? AND timeframe = ? AND complete = 1
            )
            WHERE canonical_rank = 1
            ORDER BY candle_open_at DESC LIMIT ?
            """,
            (symbol, timeframe, limit),
        ).fetchall()
    return [dict(row) for row in reversed(rows)]


def latest_forex_candle(symbol: str, timeframe: str) -> dict[str, Any] | None:
    rows = list_forex_candles(symbol, timeframe, limit=1)
    return rows[-1] if rows else None


def acquire_market_data_lock(
    lock_key: str,
    owner: str,
    *,
    stale_seconds: int,
) -> bool:
    ensure_forex_schema()
    now = datetime.now(UTC)
    with get_connection() as connection:
        connection.execute(
            "DELETE FROM forex_market_data_locks WHERE expires_at <= ?",
            (now.isoformat(),),
        )
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO forex_market_data_locks
                (lock_key, owner, acquired_at, expires_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                lock_key,
                owner,
                now.isoformat(),
                (now + timedelta(seconds=stale_seconds)).isoformat(),
            ),
        )
        return cursor.rowcount == 1


def release_market_data_lock(lock_key: str, owner: str) -> None:
    with get_connection() as connection:
        connection.execute(
            "DELETE FROM forex_market_data_locks WHERE lock_key = ? AND owner = ?",
            (lock_key, owner),
        )


def get_candle_evaluation(evaluation_key: str) -> dict[str, Any] | None:
    ensure_forex_schema()
    with get_connection() as connection:
        row = connection.execute(
            "SELECT * FROM forex_candle_evaluations WHERE evaluation_key = ?",
            (evaluation_key,),
        ).fetchone()
    return dict(row) if row else None


def save_candle_evaluation(
    *,
    evaluation_key: str,
    symbol: str,
    timeframe: str,
    candle_open_at: str,
    strategy_family: str,
    strategy_version: str,
    decision: str,
    reason: str,
    signal_id: str | None = None,
) -> None:
    ensure_forex_schema()
    with get_connection() as connection:
        connection.execute(
            """
            INSERT OR IGNORE INTO forex_candle_evaluations (
                evaluation_key, symbol, timeframe, candle_open_at,
                strategy_family, strategy_version, decision, reason,
                signal_id, evaluated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                evaluation_key, symbol, timeframe, candle_open_at,
                strategy_family, strategy_version, decision, reason,
                signal_id, datetime.now(UTC).isoformat(),
            ),
        )


def start_scan_run(
    scan_id: str,
    provider: str,
    started_at: datetime,
    timeframe: str,
    trigger_source: str,
) -> None:
    ensure_forex_schema()
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO forex_scan_runs (
                id, started_at, provider, timeframe, trigger_source, status
            )
            VALUES (?, ?, ?, ?, ?, 'RUNNING')
            """,
            (scan_id, started_at.isoformat(), provider, timeframe, trigger_source),
        )


def finish_scan_run(
    scan_id: str,
    *,
    created_count: int,
    reused_count: int,
    pairs_evaluated: int = 0,
    rejected_count: int = 0,
    telegram_queued_count: int = 0,
    result_status: str | None = None,
    rejection_reasons: list[str] | None = None,
    error_message: str | None = None,
) -> None:
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE forex_scan_runs
            SET completed_at = ?, status = ?, created_count = ?, reused_count = ?,
                pairs_evaluated = ?, candidate_count = ?, rejected_count = ?,
                telegram_queued_count = ?, result_status = ?, rejection_summary = ?,
                error_message = ?
            WHERE id = ?
            """,
            (
                datetime.now(UTC).isoformat(),
                "FAILED" if error_message else "COMPLETED",
                created_count,
                reused_count,
                pairs_evaluated,
                created_count + reused_count,
                rejected_count,
                telegram_queued_count,
                result_status or ("FAILED" if error_message else "NO_TRADE"),
                json.dumps(rejection_reasons or []),
                error_message,
                scan_id,
            ),
        )


def get_scanner_diagnostics() -> dict[str, Any]:
    ensure_forex_schema()
    with get_connection() as connection:
        latest = connection.execute(
            "SELECT * FROM forex_scan_runs ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        latest_scheduled = connection.execute(
            """
            SELECT * FROM forex_scan_runs
            WHERE trigger_source = 'scheduled'
            ORDER BY started_at DESC LIMIT 1
            """
        ).fetchone()
        latest_success = connection.execute(
            """
            SELECT * FROM forex_scan_runs
            WHERE status = 'COMPLETED'
            ORDER BY completed_at DESC LIMIT 1
            """
        ).fetchone()
        scanner_error = connection.execute(
            """
            SELECT error_message FROM forex_scan_runs
            WHERE error_message IS NOT NULL AND error_message != ''
            ORDER BY started_at DESC LIMIT 1
            """
        ).fetchone()
        telegram_error = connection.execute(
            """
            SELECT error_message FROM forex_telegram_dispatches
            WHERE error_message IS NOT NULL AND error_message != ''
            ORDER BY attempted_at DESC LIMIT 1
            """
        ).fetchone()
        queued = delivered = 0
        if latest:
            dispatch_counts = connection.execute(
                """
                SELECT
                    COUNT(d.id) AS queued,
                    SUM(CASE WHEN d.delivered_at IS NOT NULL THEN 1 ELSE 0 END) AS delivered
                FROM forex_signals s
                LEFT JOIN forex_telegram_dispatches d ON d.signal_id = s.public_id
                WHERE s.source_scan_id = ?
                """,
                (latest["id"],),
            ).fetchone()
            queued = int(dispatch_counts["queued"] or 0)
            delivered = int(dispatch_counts["delivered"] or 0)
    return {
        "last_scheduled_scan_time": _parse_datetime(
            latest_scheduled["completed_at"] or latest_scheduled["started_at"]
        ) if latest_scheduled else None,
        "last_successful_scan_time": _parse_datetime(
            latest_success["completed_at"]
        ) if latest_success else None,
        "last_scan_timeframe": latest["timeframe"] if latest else None,
        "last_trigger_source": latest["trigger_source"] if latest else None,
        "pairs_evaluated": int(latest["pairs_evaluated"] or 0) if latest else 0,
        "candidates_found": int(latest["candidate_count"] or 0) if latest else 0,
        "rejected": int(latest["rejected_count"] or 0) if latest else 0,
        "persisted": int(latest["created_count"] or 0) if latest else 0,
        "telegram_queued": queued,
        "telegram_delivered": delivered,
        "latest_scanner_error": scanner_error["error_message"] if scanner_error else None,
        "latest_telegram_error": telegram_error["error_message"] if telegram_error else None,
    }


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
        timeframe=(row["timeframe"] or row["execution_timeframe"] or "15M").upper(),
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
        latest_price=float(row["latest_price"]) if row["latest_price"] is not None else None,
        latest_price_at=_parse_datetime(row["latest_price_at"]),
        activated_entry_price=(
            float(row["activated_entry_price"])
            if row["activated_entry_price"] is not None
            else None
        ),
        tp1_hit_at=_parse_datetime(row["tp1_hit_at"]),
        tp2_hit_at=_parse_datetime(row["tp2_hit_at"]),
        stopped_at=_parse_datetime(row["stopped_at"]),
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
                timeframe,
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
                :risk_reward_1, :risk_reward_2, :timeframe, :news_risk, :spread_status, :setup_reason,
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


def list_signals(
    statuses: tuple[str, ...] | None = None,
    limit: int = 100,
    timeframe: str | None = None,
) -> list[ForexSignalPlan]:
    ensure_forex_schema()
    query = "SELECT * FROM forex_signals"
    parameters: list[Any] = []
    conditions: list[str] = []
    if statuses:
        conditions.append(f"status IN ({','.join('?' for _ in statuses)})")
        parameters.extend(statuses)
    if timeframe:
        conditions.append("timeframe = ?")
        parameters.append(timeframe.upper())
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
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
            SET status = ?,
                latest_price = ?, latest_price_at = ?,
                last_market_price = ?, last_price_updated_at = ?,
                activated_at = COALESCE(activated_at, ?),
                activated_entry_price = COALESCE(
                    activated_entry_price,
                    CASE WHEN ? IS NOT NULL THEN ? ELSE NULL END
                ),
                tp1_hit_at = COALESCE(
                    tp1_hit_at,
                    CASE WHEN ? IN ('TP1_HIT', 'TP2_HIT') THEN ? ELSE NULL END
                ),
                tp2_hit_at = COALESCE(
                    tp2_hit_at,
                    CASE WHEN ? = 'TP2_HIT' THEN ? ELSE NULL END
                ),
                stopped_at = COALESCE(
                    stopped_at,
                    CASE WHEN ? = 'STOPPED' THEN ? ELSE NULL END
                ),
                closed_at = COALESCE(closed_at, ?)
            WHERE public_id = ?
            """,
            (
                status,
                price,
                checked_at.isoformat(),
                price,
                checked_at.isoformat(),
                activated_at.isoformat() if activated_at else None,
                activated_at.isoformat() if activated_at else None,
                price,
                status,
                checked_at.isoformat(),
                status,
                checked_at.isoformat(),
                status,
                checked_at.isoformat(),
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
