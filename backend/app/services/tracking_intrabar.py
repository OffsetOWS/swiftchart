"""Settle same-candle ambiguity with finer candles.

The audited replay in tracking_rules.py cannot order an entry, target and stop
that fall inside one candle. This module re-runs that same, unchanged replay on
15-minute candles over the identical tracking window, then maps the result back
to the original timeframe's bar times. If the finer replay cannot settle the
trade, the original AMBIGUOUS outcome is kept.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
import math

from app.services.tracking_rules import replay_crypto, timeframe_duration, timestamp

INTRABAR_TIMEFRAME = "15m"
MAX_INTRABAR_CANDLES = 5000
_ACCEPTED = {"OPEN", "TP1_HIT", "SL_HIT", "TP2_HIT", "CLOSED_AFTER_TP1", "BREAK_EVEN", "EXPIRED_RUNNER", "EXPIRED_POSITION"}
_CLOSE_FIELDS = ("tp1_hit_at", "tp2_hit_at", "sl_hit_at", "closed_at")


def tracking_window_start(row: dict) -> datetime:
    """First candle open after creation, exactly as replay_crypto derives it."""
    step = timeframe_duration(row["timeframe"])
    created = timestamp(row["created_at"])
    seconds = int(step.total_seconds())
    return datetime.fromtimestamp((int(created.timestamp()) // seconds + 1) * seconds, UTC)


def intrabar_candles_needed(row: dict, as_of: datetime) -> int | None:
    """15m candles required to cover the tracking window, or None if out of reach."""
    fine = timeframe_duration(INTRABAR_TIMEFRAME)
    if timeframe_duration(row["timeframe"]) <= fine:
        return None
    needed = math.ceil((timestamp(as_of) - tracking_window_start(row)) / fine) + 8
    return needed if 0 < needed <= MAX_INTRABAR_CANDLES else None


def refine_ambiguous(row: dict, outcome: dict, fine_candles: list[dict], *, first_delivery, as_of,
                     expiry_bars: int = 12, move_to_be: bool = False) -> dict:
    if outcome.get("status") != "AMBIGUOUS":
        return outcome
    coarse = timeframe_duration(row["timeframe"])
    fine = timeframe_duration(INTRABAR_TIMEFRAME)
    if coarse <= fine or coarse % fine:
        return outcome
    ratio = coarse // fine
    start = tracking_window_start(row)
    # Shift creation to just before the window so the finer replay covers the
    # same bars. Delivery and executability still gate entry inside replay_crypto.
    fine_row = dict(row, timeframe=INTRABAR_TIMEFRAME, created_at=(start - timedelta(seconds=1)).isoformat())
    refined = replay_crypto(fine_row, fine_candles, first_delivery=first_delivery, as_of=as_of,
                            expiry_bars=expiry_bars * ratio, move_to_be=move_to_be)
    evidence = refined["tracking_evidence"]
    if refined["status"] not in _ACCEPTED or not evidence.get("validated_entry_at"):
        return outcome

    def bar_open(value):
        return start + coarse * ((timestamp(value) - start) // coarse)

    def bar_close(value):
        return start + coarse * math.ceil((timestamp(value) - start) / coarse)

    def bar_index(index):
        return math.ceil(index / ratio)

    evidence["intrabar"] = dict(
        timeframe=INTRABAR_TIMEFRAME,
        entry_at=evidence["validated_entry_at"],
        events=[dict(event) for event in evidence["events"]],
        replaced_status="AMBIGUOUS",
        replaced_flags=list(outcome.get("tracking_evidence", {}).get("flags", [])),
    )
    evidence["flags"].append("INTRABAR_15M_REFINED")
    evidence["validated_entry_at"] = bar_open(evidence["validated_entry_at"]).isoformat()
    for event in evidence["events"]:
        mapper = bar_open if event["event"] == "ENTRY_CONFIRMED" else bar_close
        event.update(at=mapper(event["at"]).isoformat(), candle=bar_index(event["candle"]))
    refined["entry_triggered_at"] = evidence["validated_entry_at"]
    for field in _CLOSE_FIELDS:
        if refined.get(field):
            refined[field] = bar_close(refined[field]).isoformat()
    if refined.get("candles_to_resolution") is not None:
        refined["candles_to_resolution"] = bar_index(refined["candles_to_resolution"])
    refined["lifecycle_events"] = json.dumps(evidence["events"], default=str)
    return refined
