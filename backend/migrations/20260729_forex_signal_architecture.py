"""Idempotent additive migration for persisted Forex signal architecture."""

from app.forex.storage import ensure_forex_schema


def upgrade() -> None:
    ensure_forex_schema()


if __name__ == "__main__":
    upgrade()
