"""Add canonical Forex candle cache, locks, and candle evaluation records."""

from app.forex.storage import ensure_forex_schema
from app.utils.database import get_connection


def upgrade() -> None:
    ensure_forex_schema()
    # Keep historical 15M rows for analytics, but remove them from the active
    # opportunity lifecycle now that the timeframe is disabled.
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE forex_signals
            SET status = 'EXPIRED', closed_at = COALESCE(closed_at, CURRENT_TIMESTAMP)
            WHERE timeframe = '15M'
              AND status IN ('PENDING_ENTRY', 'OPEN', 'TP1_HIT')
            """
        )


if __name__ == "__main__":
    upgrade()
