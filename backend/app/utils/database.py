import sqlite3
from contextlib import contextmanager
import logging
from pathlib import Path
from datetime import datetime, timedelta, timezone

from app.config import get_settings

logger = logging.getLogger(__name__)
_INITIALIZED = False


def _sqlite_path() -> Path:
    url = get_settings().database_url
    if not url.startswith("sqlite:///"):
        raise ValueError("This starter app currently supports SQLite DATABASE_URL values.")
    return Path(url.replace("sqlite:///", "", 1))


def _connect():
    path = _sqlite_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


def ensure_db() -> None:
    global _INITIALIZED
    if _INITIALIZED:
        return
    init_db()


@contextmanager
def get_connection():
    ensure_db()
    connection = _connect()
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


def init_db() -> None:
    global _INITIALIZED
    with _connect() as connection:
        logger.info("SwiftChart database connected at %s", _sqlite_path())
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS paper_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT,
                signal_id TEXT,
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                exchange TEXT NOT NULL,
                direction TEXT NOT NULL,
                entry_price REAL NOT NULL,
                stop_loss REAL NOT NULL,
                take_profit_1 REAL NOT NULL,
                take_profit_2 REAL NOT NULL,
                size REAL NOT NULL,
                risk_reward REAL,
                setup_score REAL,
                confidence REAL,
                market_bias TEXT,
                notes TEXT,
                status TEXT NOT NULL DEFAULT 'taken',
                result TEXT NOT NULL DEFAULT 'open',
                pnl REAL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                taken_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        _ensure_paper_trade_columns(connection)
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS trade_ideas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                exchange TEXT NOT NULL,
                direction TEXT NOT NULL,
                market_regime TEXT,
                higher_timeframe_bias TEXT,
                setup_score REAL,
                setup_grade TEXT,
                entry_zone_low REAL NOT NULL,
                entry_zone_high REAL NOT NULL,
                stop_loss REAL NOT NULL,
                take_profit_1 REAL NOT NULL,
                take_profit_2 REAL NOT NULL,
                risk_reward REAL NOT NULL,
                confidence REAL NOT NULL,
                reason TEXT NOT NULL,
                invalidation TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                status TEXT NOT NULL DEFAULT 'PENDING',
                outcome_checked_at TEXT,
                entry_triggered_at TEXT,
                closed_at TEXT,
                result TEXT NOT NULL DEFAULT 'OPEN',
                pnl_r_multiple REAL
            )
            """
        )
        for statement in (
            "ALTER TABLE trade_ideas ADD COLUMN regime_score REAL",
            "ALTER TABLE trade_ideas ADD COLUMN regime_label TEXT",
            "ALTER TABLE trade_ideas ADD COLUMN trend_alignment TEXT",
            "ALTER TABLE trade_ideas ADD COLUMN regime_confidence_adjustment REAL",
            "ALTER TABLE trade_ideas ADD COLUMN reversal_confirmations TEXT",
            "ALTER TABLE trade_ideas ADD COLUMN regime_explanation TEXT",
            "ALTER TABLE trade_ideas ADD COLUMN signal_candle_time TEXT",
            "ALTER TABLE trade_ideas ADD COLUMN signal_fingerprint TEXT",
            "ALTER TABLE trade_ideas ADD COLUMN lifecycle_status TEXT NOT NULL DEFAULT 'active'",
            "ALTER TABLE trade_ideas ADD COLUMN expires_at TEXT",
        ):
            try:
                connection.execute(statement)
            except sqlite3.OperationalError:
                pass
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS trade_outcomes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_idea_id INTEGER NOT NULL UNIQUE,
                status TEXT NOT NULL,
                result TEXT NOT NULL,
                entry_triggered_at TEXT,
                closed_at TEXT,
                outcome_checked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                pnl_r_multiple REAL,
                notes TEXT,
                FOREIGN KEY (trade_idea_id) REFERENCES trade_ideas(id)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS signal_reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                exchange TEXT NOT NULL,
                direction TEXT NOT NULL,
                accepted INTEGER NOT NULL,
                reason TEXT NOT NULL,
                base_score REAL,
                adjusted_score REAL,
                confidence_adjustment REAL NOT NULL DEFAULT 0,
                regime_score REAL NOT NULL,
                regime_label TEXT NOT NULL,
                trend_alignment TEXT NOT NULL,
                reversal_confirmations TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS genlayer_ai_scans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                exchange TEXT NOT NULL,
                direction TEXT NOT NULL,
                source TEXT NOT NULL,
                signal_json TEXT NOT NULL,
                decision TEXT NOT NULL,
                confidence REAL NOT NULL,
                risk_level TEXT NOT NULL,
                validator_reasoning TEXT NOT NULL,
                validator_votes_json TEXT NOT NULL,
                recommended_position_size REAL NOT NULL,
                warning_flags_json TEXT NOT NULL,
                paper_execution_status TEXT NOT NULL DEFAULT 'NOT_EXECUTED',
                final_trade_outcome TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS profiles (
                user_id TEXT PRIMARY KEY,
                email TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_idea_id INTEGER UNIQUE,
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                exchange TEXT NOT NULL,
                direction TEXT NOT NULL,
                signal_json TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS user_taken_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                signal_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                exchange TEXT NOT NULL,
                direction TEXT NOT NULL,
                entry_price REAL NOT NULL,
                stop_loss REAL NOT NULL,
                take_profit_1 REAL NOT NULL,
                take_profit_2 REAL NOT NULL,
                risk_reward REAL,
                setup_score REAL,
                confidence REAL,
                market_bias TEXT,
                market_regime TEXT,
                liquidity_status TEXT,
                signal_timestamp TEXT,
                status TEXT NOT NULL DEFAULT 'taken',
                result TEXT NOT NULL DEFAULT 'open',
                pnl REAL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                taken_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, signal_id)
            )
            """
        )
        _ensure_user_taken_trade_columns(connection)
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS webhook_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                event TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS execution_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_id TEXT,
                symbol TEXT,
                status TEXT NOT NULL,
                reason TEXT,
                payload_json TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS api_usage_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                route TEXT NOT NULL,
                method TEXT NOT NULL,
                client_ip TEXT,
                status_code INTEGER,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS api_webhook_nonces (
                nonce TEXT PRIMARY KEY,
                timestamp INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute("CREATE INDEX IF NOT EXISTS idx_trade_ideas_created_at ON trade_ideas(created_at)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_trade_ideas_symbol ON trade_ideas(symbol)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_trade_ideas_status ON trade_ideas(status)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_trade_ideas_result ON trade_ideas(result)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_trade_ideas_dedupe ON trade_ideas(symbol, timeframe, exchange, direction, entry_zone_low, entry_zone_high, stop_loss, take_profit_1, take_profit_2, created_at)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_trade_ideas_fingerprint ON trade_ideas(signal_fingerprint)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_trade_ideas_lifecycle ON trade_ideas(lifecycle_status)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_signal_reviews_created_at ON signal_reviews(created_at)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_signal_reviews_regime ON signal_reviews(regime_label)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_genlayer_ai_scans_created_at ON genlayer_ai_scans(created_at)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_genlayer_ai_scans_signal ON genlayer_ai_scans(symbol, timeframe, exchange, direction, created_at)")
        connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_paper_trades_user_signal ON paper_trades(user_id, signal_id) WHERE user_id IS NOT NULL AND signal_id IS NOT NULL")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_signals_created_at ON signals(created_at)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_signals_symbol ON signals(symbol)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_user_taken_trades_user ON user_taken_trades(user_id, taken_at DESC)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_webhook_events_created_at ON webhook_events(created_at)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_execution_logs_created_at ON execution_logs(created_at)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_api_usage_logs_route ON api_usage_logs(route, created_at)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_api_webhook_nonces_created_at ON api_webhook_nonces(created_at)")
        _INITIALIZED = True


def _ensure_paper_trade_columns(connection: sqlite3.Connection) -> None:
    columns = {row["name"] for row in connection.execute("PRAGMA table_info(paper_trades)").fetchall()}
    additions = {
        "user_id": "TEXT",
        "signal_id": "TEXT",
        "risk_reward": "REAL",
        "setup_score": "REAL",
        "confidence": "REAL",
        "market_bias": "TEXT",
        "result": "TEXT NOT NULL DEFAULT 'open'",
        "pnl": "REAL",
        "taken_at": "TEXT",
    }
    for column, definition in additions.items():
        if column not in columns:
            connection.execute(f"ALTER TABLE paper_trades ADD COLUMN {column} {definition}")
    connection.execute("UPDATE paper_trades SET status = 'taken' WHERE status = 'open'")
    connection.execute("UPDATE paper_trades SET taken_at = COALESCE(taken_at, created_at, CURRENT_TIMESTAMP)")


def _ensure_user_taken_trade_columns(connection: sqlite3.Connection) -> None:
    columns = {row["name"] for row in connection.execute("PRAGMA table_info(user_taken_trades)").fetchall()}
    additions = {
        "market_regime": "TEXT",
        "liquidity_status": "TEXT",
        "signal_timestamp": "TEXT",
    }
    for column, definition in additions.items():
        if column not in columns:
            connection.execute(f"ALTER TABLE user_taken_trades ADD COLUMN {column} {definition}")


def claim_api_nonce(nonce: str, timestamp: int, ttl_seconds: int) -> bool:
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=ttl_seconds)).strftime("%Y-%m-%d %H:%M:%S")
    try:
        with get_connection() as connection:
            connection.execute("DELETE FROM api_webhook_nonces WHERE created_at < ?", (cutoff,))
            connection.execute(
                "INSERT INTO api_webhook_nonces (nonce, timestamp) VALUES (?, ?)",
                (nonce, int(timestamp)),
            )
    except sqlite3.IntegrityError:
        return False
    return True
