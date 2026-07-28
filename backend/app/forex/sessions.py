from __future__ import annotations

from datetime import UTC, datetime, time, timedelta

from app.forex.models import ForexSessionState


SESSION_WINDOWS_UTC = {
    "Tokyo": (time(0, 0), time(9, 0)),
    "London": (time(7, 0), time(16, 0)),
    "New York": (time(12, 0), time(21, 0)),
}


def _minutes_since_midnight(value: datetime) -> int:
    return value.hour * 60 + value.minute


def _time_to_minutes(value: time) -> int:
    return value.hour * 60 + value.minute


def _in_window(now_minutes: int, start: time, end: time) -> bool:
    start_minutes = _time_to_minutes(start)
    end_minutes = _time_to_minutes(end)
    if start_minutes <= end_minutes:
        return start_minutes <= now_minutes < end_minutes
    return now_minutes >= start_minutes or now_minutes < end_minutes


def _next_open(now: datetime) -> tuple[str, datetime, int]:
    candidates: list[tuple[str, datetime, int]] = []
    for session, (start, _) in SESSION_WINDOWS_UTC.items():
        open_at = datetime.combine(now.date(), start, tzinfo=UTC)
        if open_at <= now:
            open_at += timedelta(days=1)
        minutes = int((open_at - now).total_seconds() // 60)
        candidates.append((session, open_at, minutes))
    return min(candidates, key=lambda item: item[2])


def forex_session_state(now: datetime | None = None) -> ForexSessionState:
    current = now or datetime.now(UTC)
    current = current if current.tzinfo else current.replace(tzinfo=UTC)
    current = current.astimezone(UTC)
    market_open = current.weekday() < 5
    now_minutes = _minutes_since_midnight(current)
    active = [
        session
        for session, (start, end) in SESSION_WINDOWS_UTC.items()
        if market_open and _in_window(now_minutes, start, end)
    ]
    is_overlap = "London" in active and "New York" in active
    active_session = "London-New York overlap" if is_overlap else active[0] if active else "Closed"
    next_session, next_open, minutes_until = _next_open(current)
    if not market_open and current.weekday() >= 5:
        days_until_monday = (7 - current.weekday()) % 7 or 7
        next_open = datetime.combine((current + timedelta(days=days_until_monday)).date(), SESSION_WINDOWS_UTC["Tokyo"][0], tzinfo=UTC)
        next_session = "Tokyo"
        minutes_until = int((next_open - current).total_seconds() // 60)
    pre_session = 0 <= minutes_until <= 60
    session_open = any(
        abs(now_minutes - _time_to_minutes(start)) <= 60
        for start, _ in SESSION_WINDOWS_UTC.values()
    ) and market_open
    if not market_open:
        label = "Forex market is currently closed. Next session opens soon."
    elif pre_session:
        label = f"Pre-session: {next_session} opens in {minutes_until} minutes."
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
        is_session_open=session_open,
        is_overlap=is_overlap,
        market_open=market_open,
        label=label,
    )
