"""Add canonical independent timeframe support to persisted Forex signals."""

from app.forex.storage import ensure_forex_schema


def upgrade() -> None:
    ensure_forex_schema()


if __name__ == "__main__":
    upgrade()
