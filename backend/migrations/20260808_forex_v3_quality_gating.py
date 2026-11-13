"""Add persisted Forex v3 retest and quality-gate audit fields."""

from app.forex.storage import ensure_forex_schema


def upgrade() -> None:
    ensure_forex_schema()


if __name__ == "__main__":
    upgrade()
