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
            "ALTER TABLE trade_ideas ADD COLUMN tp1_hit_at TEXT",
            "ALTER TABLE trade_ideas ADD COLUMN tp2_hit_at TEXT",
            "ALTER TABLE trade_ideas ADD COLUMN sl_hit_at TEXT",
            "ALTER TABLE trade_ideas ADD COLUMN expired_at TEXT",
            "ALTER TABLE trade_ideas ADD COLUMN candles_to_resolution INTEGER",
            "ALTER TABLE trade_ideas ADD COLUMN lifecycle_events TEXT",
            "ALTER TABLE trade_ideas ADD COLUMN entry_status TEXT",
            "ALTER TABLE trade_ideas ADD COLUMN setup_family TEXT",
            "ALTER TABLE trade_ideas ADD COLUMN strategy_version TEXT",
            "ALTER TABLE trade_ideas ADD COLUMN edge_status TEXT",
            "ALTER TABLE trade_ideas ADD COLUMN strategy_decision TEXT",
            "ALTER TABLE trade_ideas ADD COLUMN v2_decision_reason TEXT",
            "ALTER TABLE trade_ideas ADD COLUMN regime_confidence REAL",
            "ALTER TABLE trade_ideas ADD COLUMN entry_quality_status TEXT",
            "ALTER TABLE trade_ideas ADD COLUMN entry_quality_score REAL",
            "ALTER TABLE trade_ideas ADD COLUMN entry_quality_reason TEXT",
            "ALTER TABLE trade_ideas ADD COLUMN outcome_tracking_mode TEXT",
            "ALTER TABLE trade_ideas ADD COLUMN v2_evaluated_at TEXT",
            "ALTER TABLE trade_ideas ADD COLUMN opportunity_key TEXT",
            "ALTER TABLE trade_ideas ADD COLUMN retest_confirmed_at TEXT",
            "ALTER TABLE trade_ideas ADD COLUMN executable_at TEXT",
            "ALTER TABLE trade_ideas ADD COLUMN production_rule_accepted INTEGER",
            "ALTER TABLE trade_ideas ADD COLUMN strict_trend_short_eligible INTEGER",
            "ALTER TABLE trade_ideas ADD COLUMN strict_trigger_type TEXT",
            "ALTER TABLE trade_ideas ADD COLUMN strict_confirmation_type TEXT",
            "ALTER TABLE trade_ideas ADD COLUMN strict_trigger_candle_time TEXT",
            "ALTER TABLE trade_ideas ADD COLUMN strict_trigger_candle_completed INTEGER",
        ):
            try:
                connection.execute(statement)
            except sqlite3.OperationalError:
                pass
        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_trade_ideas_opportunity_key "
            "ON trade_ideas(opportunity_key) WHERE opportunity_key IS NOT NULL"
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
                tp1_hit_at TEXT,
                tp2_hit_at TEXT,
                sl_hit_at TEXT,
                expired_at TEXT,
                candles_to_resolution INTEGER,
                lifecycle_events TEXT,
                notes TEXT,
                FOREIGN KEY (trade_idea_id) REFERENCES trade_ideas(id)
            )
            """
        )
        for statement in (
            "ALTER TABLE trade_outcomes ADD COLUMN tp1_hit_at TEXT",
            "ALTER TABLE trade_outcomes ADD COLUMN tp2_hit_at TEXT",
            "ALTER TABLE trade_outcomes ADD COLUMN sl_hit_at TEXT",
            "ALTER TABLE trade_outcomes ADD COLUMN expired_at TEXT",
            "ALTER TABLE trade_outcomes ADD COLUMN candles_to_resolution INTEGER",
            "ALTER TABLE trade_outcomes ADD COLUMN lifecycle_events TEXT",
        ):
            try:
                connection.execute(statement)
            except sqlite3.OperationalError:
                pass
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
        connection.execute("CREATE INDEX IF NOT EXISTS idx_trade_ideas_created_at ON trade_ideas(created_at)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_trade_ideas_symbol ON trade_ideas(symbol)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_trade_ideas_status ON trade_ideas(status)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_trade_ideas_result ON trade_ideas(result)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_trade_ideas_dedupe ON trade_ideas(symbol, timeframe, exchange, direction, entry_zone_low, entry_zone_high, stop_loss, take_profit_1, take_profit_2, created_at)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_trade_ideas_fingerprint ON trade_ideas(signal_fingerprint)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_trade_ideas_lifecycle ON trade_ideas(lifecycle_status)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_trade_ideas_v2_strategy ON trade_ideas(setup_family, strategy_version, edge_status, strategy_decision)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_signal_reviews_created_at ON signal_reviews(created_at)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_signal_reviews_regime ON signal_reviews(regime_label)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_genlayer_ai_scans_created_at ON genlayer_ai_scans(created_at)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_genlayer_ai_scans_signal ON genlayer_ai_scans(symbol, timeframe, exchange, direction, created_at)")
        connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_paper_trades_user_signal ON paper_trades(user_id, signal_id) WHERE user_id IS NOT NULL AND signal_id IS NOT NULL")
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
