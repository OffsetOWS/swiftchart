import sqlite3
from contextlib import contextmanager
import logging
from pathlib import Path

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
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                exchange TEXT NOT NULL,
                direction TEXT NOT NULL,
                entry_price REAL NOT NULL,
                stop_loss REAL NOT NULL,
                take_profit_1 REAL NOT NULL,
                take_profit_2 REAL NOT NULL,
                size REAL NOT NULL,
                notes TEXT,
                status TEXT NOT NULL DEFAULT 'open',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
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
        connection.execute("CREATE INDEX IF NOT EXISTS idx_trade_ideas_created_at ON trade_ideas(created_at)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_trade_ideas_symbol ON trade_ideas(symbol)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_trade_ideas_status ON trade_ideas(status)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_trade_ideas_result ON trade_ideas(result)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_trade_ideas_dedupe ON trade_ideas(symbol, timeframe, exchange, direction, entry_zone_low, entry_zone_high, stop_loss, take_profit_1, take_profit_2, created_at)")
    _INITIALIZED = True
