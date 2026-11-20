"""The crypto deadline is shared by the tracker, outbox and public detail."""

from datetime import UTC, datetime, timedelta
from app.services.tracking_rules import timestamp, timeframe_duration

HOURS = 48


def expiry_bars(row):
    step = timeframe_duration(row["timeframe"])
    value = timedelta(hours=HOURS) / step
    if value < 1 or int(value) != value:
        raise ValueError("Unsupported 48-hour tracking grid")
    return int(value)


def deadline(created_at, timeframe):
    created = timestamp(created_at)
    step = timeframe_duration(timeframe)
    seconds = int(step.total_seconds())
    first = datetime.fromtimestamp(
        (int(created.timestamp()) // seconds + 1) * seconds, UTC
    )
    return first + timedelta(hours=HOURS)


def deadline_text(created_at, timeframe):
    return deadline(created_at, timeframe).strftime("%Y-%m-%d %H:%M UTC")
