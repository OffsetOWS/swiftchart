"""Modeled trading costs for tracked crypto outcomes.

Kept separate from tracking_rules.py so the audited replay rules stay unchanged.
Cost = bps per side on entry notional plus quantity-weighted exit notionals,
expressed in R (divided by initial stop distance). Funding is recorded separately.
"""
from __future__ import annotations

import math
import json

from app.services.tracking_rules import zone_bounds

# Exit legs implied by each terminal status under the 50% TP1 / 50% runner contract.
_LEGS = {
    "SL_HIT": (("stop", 1.0),),
    "TP2_HIT": (("tp1", 0.5), ("tp2", 0.5)),
    "CLOSED_AFTER_TP1": (("tp1", 0.5), ("stop", 0.5)),
    "BREAK_EVEN": (("tp1", 0.5), ("entry", 0.5)),
}


def cost_r(row: dict, bps_per_side: float) -> float | None:
    """Modeled cost in R for a resolved row, or None when it cannot be derived."""
    legs = _LEGS.get(str(row.get("status") or ""))
    if row.get("status") == "TIME_EXIT":
        events = row.get("lifecycle_events") or []
        events = json.loads(events) if isinstance(events, str) else events
        time_exit = next((e for e in reversed(events) if e.get("event") == "TIME_EXIT"), None)
        if time_exit is None:
            return None
        legs = (("time_exit", 1.0),) if time_exit["quantity"] == 1 else (("tp1", 0.5), ("time_exit", 0.5))
    if legs is None or row.get("pnl_r_multiple") is None:
        return None
    try:
        low, high = zone_bounds(row["entry_zone_low"], row["entry_zone_high"])
        prices = {
            "entry": (low + high) / 2,
            "stop": float(row["stop_loss"]),
            "tp1": float(row["take_profit_1"]),
            "tp2": float(row["take_profit_2"]),
        }
        if row.get("status") == "TIME_EXIT":
            prices["time_exit"] = float(time_exit["price"])
    except (KeyError, TypeError, ValueError):
        return None
    risk = abs(prices["entry"] - prices["stop"])
    if not all(math.isfinite(value) for value in prices.values()) or risk <= 0:
        return None
    notional = prices["entry"] + sum(quantity * prices[name] for name, quantity in legs)
    return max(0.0, float(bps_per_side)) / 10_000 * notional / risk


def net_r(row: dict, bps_per_side: float) -> float | None:
    cost = cost_r(row, bps_per_side)
    funding = (row.get("tracking_evidence") or {}).get("costs", {})
    if funding:
        if funding.get("funding_r") is None:
            return None
        return None if cost is None else float(row["pnl_r_multiple"]) - cost - float(funding["funding_r"])
    return None if cost is None else float(row["pnl_r_multiple"]) - cost
