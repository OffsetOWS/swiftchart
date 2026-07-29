from __future__ import annotations

from datetime import UTC, datetime, time, timedelta

from app.forex.models import ForexSessionState


SESSION_WINDOWS_UTC = {
    "Tokyo": (time(0, 0), time(9, 0)),
    "London": (time(7, 0), time(16, 0)),
    "New York": (time(12, 0), time(21, 0)),
}


def _minutes(value: time) -> int:
    return value.hour * 60 + value.minute


def _in_window(now_minutes: int, start: time, end: time) -> bool:
    start_minutes, end_minutes = _minutes(start), _minutes(end)
    return start_minutes <= now_minutes < end_minutes


def _next_open(now: datetime) -> tuple[str, datetime, int]:
    candidates = []
    for session, (start, _) in SESSION_WINDOWS_UTC.items():
        open_at = datetime.combine(now.date(), start, tzinfo=UTC)
        if open_at <= now:
            open_at += timedelta(days=1)
        candidates.append((session, open_at, int((open_at - now).total_seconds() // 60)))
    return min(candidates, key=lambda item: item[2])


def forex_session_state(now: datetime | None = None) -> ForexSessionState:
    current = (now or datetime.now(UTC)).astimezone(UTC)
    market_open = current.weekday() < 5
    now_minutes = current.hour * 60 + current.minute
    active = [
        name
        for name, (start, end) in SESSION_WINDOWS_UTC.items()
        if market_open and _in_window(now_minutes, start, end)
    ]
    overlap = "London" in active and "New York" in active
    active_session = "London-New York overlap" if overlap else active[0] if active else "Closed"
    next_session, next_open, minutes_until = _next_open(current)
    if not market_open:
        days_until_monday = (7 - current.weekday()) % 7 or 7
        next_open = datetime.combine(
            (current + timedelta(days=days_until_monday)).date(),
            SESSION_WINDOWS_UTC["Tokyo"][0],
            tzinfo=UTC,
        )
        next_session = "Tokyo"
        minutes_until = int((next_open - current).total_seconds() // 60)
    pre_session = 0 <= minutes_until <= 60
    if not market_open:
        label = "Forex market is currently closed."
    elif active_session != "Closed":
        label = f"Active session: {active_session}."
    else:
        label = f"Between sessions. Next: {next_session}."
    return ForexSessionState(
        active_session=active_session,
        next_session=next_session,
        next_session_open=next_open,
        time_until_next_session_minutes=max(0, minutes_until),
        is_pre_session=pre_session,
        is_session_open=bool(active),
        is_overlap=overlap,
        market_open=market_open,
        label=label,
    )
