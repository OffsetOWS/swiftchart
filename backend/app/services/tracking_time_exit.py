"""Explicit modeled time liquidation layered over the unchanged audited replay."""
from copy import deepcopy
import json
import math

from app.services.tracking_rules import account_exit_legs, completed, timestamp, timeframe_duration

TIME_EXIT_POLICY = "tracking-window-close-v1"


def apply_time_exit(row: dict, outcome: dict, candles: list[dict]) -> dict:
    evidence = outcome.get("tracking_evidence", {})
    if (outcome.get("status") not in {"EXPIRED_POSITION", "EXPIRED_RUNNER"}
            or not evidence.get("validated_entry_at") or not evidence.get("mature")):
        return outcome
    horizon = timestamp(evidence["horizon_end"])
    step = timeframe_duration(row["timeframe"])
    final = [bar for bar in candles if timestamp(bar["timestamp"]) + step == horizon]
    if len(final) != 1 or not completed(final[0].get("complete", True)):
        return outcome
    price = float(final[0]["close"])
    if not math.isfinite(price) or price <= 0:
        return outcome
    result = deepcopy(outcome)
    evidence = result["tracking_evidence"]
    quantity = evidence["remaining_quantity"]
    if not 0 < quantity <= 1:
        return outcome
    evidence["fills"].append(dict(kind="TIME_EXIT", price=price, quantity=quantity,
                                   candle_close_at=horizon.isoformat()))
    entry = evidence["midpoint"]
    evidence.update(account_exit_legs(entry, row["stop_loss"], row["direction"], evidence["fills"]))
    pnl = evidence["booked_r"]
    evidence.update(category="resolved", time_exit_policy=TIME_EXIT_POLICY)
    evidence["flags"].append("MODELED_TIME_EXIT")
    event = dict(event="TIME_EXIT", at=horizon.isoformat(), candle=result["candles_to_resolution"],
                 price=price, quantity=quantity, policy=TIME_EXIT_POLICY)
    evidence["events"].append(event)
    result.update(status="TIME_EXIT", closed_at=horizon.isoformat(), pnl_r_multiple=pnl,
                  result="WIN" if pnl > 0 else "LOSS" if pnl < 0 else "BREAK_EVEN",
                  lifecycle_events=json.dumps(evidence["events"], default=str))
    return result
