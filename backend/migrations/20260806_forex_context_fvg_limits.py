"""Ensure Forex signal and shadow limit-opportunity persistence."""

from app.forex.limit_storage import ensure_limit_opportunity_schema
from app.forex.storage import ensure_forex_schema


def upgrade() -> None:
    ensure_forex_schema()
    ensure_limit_opportunity_schema()


if __name__ == "__main__":
    upgrade()
