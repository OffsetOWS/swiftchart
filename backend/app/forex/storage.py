from __future__ import annotations

from app.forex.models import ForexSignal
from app.utils.database import get_connection


def ensure_forex_signal_table() -> None:
    with get_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS forex_signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                market_type TEXT NOT NULL DEFAULT 'forex',
                pair TEXT NOT NULL,
                direction TEXT NOT NULL,
                score REAL NOT NULL,
                grade TEXT NOT NULL,
                session TEXT,
                entry REAL,
                stop_loss REAL,
                tp1 REAL,
                tp2 REAL,
                rr REAL,
                news_risk TEXT,
                spread_status TEXT,
                reason TEXT,
                status TEXT NOT NULL DEFAULT 'wait',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )


def save_forex_signals(signals: list[ForexSignal]) -> int:
    if not signals:
        return 0
    ensure_forex_signal_table()
    with get_connection() as connection:
        for signal in signals:
            connection.execute(
                """
                INSERT INTO forex_signals (
                    market_type, pair, direction, score, grade, session, entry,
                    stop_loss, tp1, tp2, rr, news_risk, spread_status, reason,
                    status, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "forex",
                    signal.pair,
                    signal.direction,
                    signal.score,
                    signal.grade,
                    signal.session,
                    signal.entry,
                    signal.stopLoss,
                    signal.tp1,
                    signal.tp2,
                    signal.rr,
                    signal.newsRisk,
                    signal.spreadStatus,
                    signal.reason,
                    signal.status,
                    signal.lastUpdated.isoformat(),
                ),
            )
    return len(signals)
