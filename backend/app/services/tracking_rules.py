"""Versioned evidence-based tracking. No strategy selection or order execution.

The original signal is immutable input. A completed OHLC midpoint touch is a
modeled fill, not an exchange receipt. Missing/contradictory evidence fails closed.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
import json
import math

RULE_VERSION = "tracking-v3-delivery-order-quantity"
IMPLEMENTATION_REVISION = "reliability-2026-10-22"


def completed(value):
    return value is True or value == 1 or value in ("1", "true", "True")


def account_exit_legs(entry, stop, direction, legs):
    """Account quantities as fractions of original exposure, never target labels.

    Callers must supply evidenced quantities; this helper does not infer fills.
    Invalid/missing quantities must remain unresolved at the calling boundary.
    """
    entry, stop = float(entry), float(stop)
    if direction not in {'LONG', 'SHORT'} or not all(math.isfinite(x) for x in (entry,stop)) or entry == stop:
        raise ValueError('Invalid entry risk')
    side = 1 if direction == 'LONG' else -1
    quantity = value = 0.
    for leg in legs:
        q,p = float(leg['quantity']),float(leg['price'])
        if not all(math.isfinite(x) for x in (q,p)) or q <= 0:
            raise ValueError('Invalid exit quantity or price')
        quantity += q
        if quantity > 1+1e-12:
            raise ValueError('Exits exceed original exposure')
        value += q*side*(p-entry)/abs(entry-stop)
    return dict(booked_r=value,remaining_quantity=max(0.,1-quantity))


def timestamp(value):
    if value is None or value == "":
        return None
    value = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)


def zone_bounds(a, b):
    a, b = float(a), float(b)
    if not all(math.isfinite(v) for v in (a, b)):
        raise ValueError("Nonfinite entry zone")
    return min(a, b), max(a, b)


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str, allow_nan=False).encode()).hexdigest()


def timeframe_duration(timeframe):
    value = str(timeframe).lower()
    return timedelta(minutes=int(value[:-1]) * {"m": 1, "h": 60, "d": 1440}[value[-1]])


def replay_crypto(row, candles, *, first_delivery, as_of, expiry_bars=12, move_to_be=False):
    """Replay a complete, contiguous creation-based horizon, preserving claims.

    Same-entry-bar exits are unresolved. After entry, horizon expiry is not an
    execution. The historical 12-bar tracking horizon is retained; expires_at is
    exposed as a conflict, not silently substituted for a new strategy deadline.
    """
    as_of = timestamp(as_of)
    created = timestamp(row["created_at"])
    step = timeframe_duration(row["timeframe"])
    first_open = datetime.fromtimestamp((int(created.timestamp()) // int(step.total_seconds()) + 1) * step.total_seconds(), UTC)
    horizon = first_open + step * expiry_bars
    lo, hi = zone_bounds(row["entry_zone_low"], row["entry_zone_high"])
    entry = (lo + hi) / 2
    stop, tp1, tp2 = (float(row[k]) for k in ("stop_loss", "take_profit_1", "take_profit_2"))
    side = 1 if row["direction"] == "LONG" else -1
    risk = side * (entry - stop)
    flags = ["INVERTED_ENTRY_ZONE"] if float(row["entry_zone_low"]) > float(row["entry_zone_high"]) else []
    previous_events = row.get("lifecycle_events") or []
    if isinstance(previous_events, str):
        previous_events = json.loads(previous_events)
    claim = row.get("entry_triggered_at") or next((e.get("at") for e in previous_events if e.get("event") in {"ENTRY_TRIGGERED", "ENTRY_CONFIRMED"}), None)
    out = dict(status="OPEN", result="OPEN", entry_triggered_at=claim,
               tp1_hit_at=None, tp2_hit_at=None, sl_hit_at=None, expired_at=None,
               closed_at=None, outcome_checked_at=as_of.isoformat(), pnl_r_multiple=None,
               candles_to_resolution=None, lifecycle_events="[]")
    evidence = dict(rule_version=RULE_VERSION, original_entry_claim=claim,
                    normalized_entry_zone=[lo, hi], midpoint=entry,
                    first_delivery=first_delivery, validated_entry_at=None,
                    horizon_end=horizon.isoformat(), mature=as_of >= horizon,
                    category="unresolved", flags=flags, fills=[], events=[], booked_r=0.0,
                    remaining_quantity=1.0, candle_hash=None)
    out["tracking_evidence"] = evidence

    def finish(status, result="UNRESOLVED", pnl=None, at=None, index=None):
        out.update(status=status, result=result, pnl_r_multiple=pnl, candles_to_resolution=index)
        if at is not None and pnl is not None:
            out["closed_at"] = at.isoformat()
        evidence["category"] = "resolved" if pnl is not None else "unentered" if result == "NO_ENTRY" else "unresolved"
        # Keep claimed history separate from validated fills; never erase it.
        out["lifecycle_events"] = json.dumps(evidence["events"], default=str)
        return out

    try:
        evidence["candle_hash"] = fingerprint(candles)
    except (ValueError, TypeError):
        flags.append("INVALID_CANDLE")
        return finish("UNRESOLVED")
    if row["direction"] not in {"LONG", "SHORT"} or not all(math.isfinite(x) for x in (entry, stop, tp1, tp2)) or risk <= 0 or side*(tp1-entry) <= 0 or side*(tp2-tp1) < 0:
        flags.append("INVALID_PRICE_GEOMETRY")
        return finish("UNRESOLVED")
    if first_delivery is None:
        flags.append("MISSING_DELIVERY_EVIDENCE")
        return finish("UNRESOLVED")
    available = max(timestamp(first_delivery), timestamp(row.get("executable_at")) or created, created)
    r1, r2 = side*(tp1-entry)/risk, side*(tp2-entry)/risk
    by_open = {}
    for bar in candles:
        try:
            opened = timestamp(bar["timestamp"])
            if opened is None:
                raise ValueError("Missing timestamp")
            if not first_open <= opened < horizon:
                continue
            prices = [float(bar[k]) for k in ("open", "high", "low", "close")]
            if not all(math.isfinite(p) for p in prices):
                raise ValueError("Nonfinite candle")
        except (KeyError, TypeError, ValueError):
            flags.append("INVALID_CANDLE")
            return finish("UNRESOLVED")
        if opened in by_open and by_open[opened] != bar:
            flags.append("CONFLICTING_CANDLE_VERSIONS")
            return finish("UNRESOLVED")
        by_open[opened] = bar
    if claim:
        oldbar = by_open.get(timestamp(claim))
        if oldbar is None or not float(oldbar["low"]) <= entry <= float(oldbar["high"]):
            flags.append("RECORDED_ENTRY_MISSES_MIDPOINT_OR_MISSING")
        if timestamp(claim) < available:
            flags.append("RECORDED_ENTRY_PRE_DELIVERY")
        if timestamp(claim) + step > as_of:
            flags.append("RECORDED_ENTRY_INCOMPLETE")
    entered = False
    partial = False

    def fill(kind, price, quantity, at):
        value = quantity * side * (price-entry)/risk
        evidence["fills"].append(dict(kind=kind, price=price, quantity=quantity, r=value, candle_close_at=at.isoformat()))
        evidence.update(account_exit_legs(entry, stop, row['direction'], evidence['fills']))

    def event(kind, at, index):
        evidence["events"].append(dict(event=kind, at=at.isoformat(), candle=index, rule_version=RULE_VERSION))

    for index in range(1, expiry_bars+1):
        opened = first_open + step*(index-1)
        closed = opened + step
        if closed > as_of:
            break
        bar = by_open.get(opened)
        if bar is None or not completed(bar.get("complete", True)):
            flags.append("MISSING_COMPLETED_CANDLE")
            return finish("UNRESOLVED")
        low, high = float(bar["low"]), float(bar["high"])
        if not all(math.isfinite(float(bar[k])) for k in ("open", "high", "low", "close")) or not low <= min(float(bar["open"]), float(bar["close"])) <= max(float(bar["open"]),float(bar["close"])) <= high:
            flags.append("INVALID_CANDLE")
            return finish("UNRESOLVED")
        if opened < available:
            if low <= entry <= high:
                flags.append("PARTIAL_PRE_DELIVERY_BAR_EXCLUDED")
                if available < closed:
                    # The touch may have occurred after delivery. Neither a fill
                    # nor absence of a fill can be established from this OHLC bar.
                    flags.append("DELIVERY_BAR_ORDER_UNKNOWN")
                    return finish("UNRESOLVED")
            continue
        new_entry = False
        if not entered:
            if not low <= entry <= high:
                continue
            entered = new_entry = True
            evidence["validated_entry_at"] = opened.isoformat()
            # Correct the projection; retain the original claim in evidence/ledger.
            out["entry_triggered_at"] = opened.isoformat()
            event("ENTRY_CONFIRMED", opened, index)
            if row.get("expires_at") and opened >= timestamp(row["expires_at"]):
                flags.append("ENTRY_AFTER_DECLARED_EXPIRY_POLICY_CONFLICT")
        hit1 = high >= tp1 if side == 1 else low <= tp1
        hit2 = high >= tp2 if side == 1 else low <= tp2
        active_stop = entry if partial and move_to_be else stop
        hitstop = low <= active_stop if side == 1 else high >= active_stop
        if (new_entry and (hit1 or hitstop)) or (hitstop and (hit2 or (hit1 and not partial))):
            flags.append("AMBIGUOUS_SAME_CANDLE_ORDER")
            event("AMBIGUOUS", closed, index)
            return finish("AMBIGUOUS", at=closed, index=index)
        if hitstop:
            fill("RUNNER_STOP" if partial else "STOP", active_stop, .5 if partial else 1., closed)
            out["sl_hit_at"] = closed.isoformat()
            status = "BREAK_EVEN" if partial and move_to_be else "CLOSED_AFTER_TP1" if partial else "SL_HIT"
            event(status, closed, index)
            value = evidence["booked_r"]
            return finish(status, "WIN" if value>0 else "LOSS" if value<0 else "BREAK_EVEN", value, closed, index)
        if hit1 and not partial:
            # A BE stop touched in the TP1 bar may occur after TP1; don't invent ordering.
            if move_to_be and (low <= entry if side == 1 else high >= entry):
                flags.append("AMBIGUOUS_TP1_BE_ORDER")
                return finish("AMBIGUOUS", index=index)
            partial = True
            fill("TP1", tp1, .5, closed)
            out["tp1_hit_at"] = closed.isoformat()
            event("TP1_HIT", closed, index)
        if hit2:
            fill("TP2", tp2, .5, closed)
            out["tp2_hit_at"] = closed.isoformat()
            event("TP2_HIT", closed, index)
            return finish("TP2_HIT", "WIN", evidence["booked_r"], closed, index)
    if as_of >= horizon:
        out["expired_at"] = horizon.isoformat()
        if entered:
            event("TRACKING_HORIZON_ENDED_WITH_EXPOSURE", horizon, expiry_bars)
            return finish("EXPIRED_RUNNER" if partial else "EXPIRED_POSITION", index=expiry_bars)
        if claim:
            flags.append("UNPROVEN_HISTORICAL_ENTRY")
            return finish("UNRESOLVED", index=expiry_bars)
        return finish("EXPIRED", "NO_ENTRY", index=expiry_bars)
    return finish("TP1_HIT" if partial else "OPEN", "OPEN")


def forex_provider_evidence(row, candles, *, as_of):
    """Do not splice providers/grids. Preserve disagreements, even against a terminal row.

    This validates close-trigger tracking only. No claim of intrabar stop fills.
    Missing provider lineage or an activated position with no coverage is unresolved.
    """
    activated = timestamp(row.get("activated_at"))
    evidence = dict(rule_version=RULE_VERSION, provider_results={}, flags=[], candles=candles)
    if activated is None:
        evidence["flags"].append("NO_ACTIVATION_EVIDENCE")
        evidence["unresolved"] = True
        return evidence
    groups = {}
    for c in candles:
        closed = timestamp(c.get("candle_close_at"))
        if not completed(c.get("complete")) or not closed or not activated < closed <= timestamp(as_of):
            continue
        groups.setdefault(c.get("provider") or "UNKNOWN", []).append(c)
    side = 1 if row["direction"] == "LONG" else -1
    for provider, bars in groups.items():
        result = "OPEN"
        at = None
        versions = {}
        invalid = False
        for bar in bars:
            key = timestamp(bar["candle_close_at"])
            try:
                price = float(bar["close"])
                if not math.isfinite(price):
                    raise ValueError("Nonfinite price")
            except (TypeError, ValueError, KeyError):
                evidence["flags"].append("INVALID_PROVIDER_CANDLE")
                invalid = True
                continue
            if key in versions and versions[key] != price:
                evidence["flags"].append("CONFLICTING_PROVIDER_CANDLES")
                invalid = True
            versions[key] = price
        if invalid:
            evidence["provider_results"][provider] = dict(status="UNRESOLVED", at=None)
            continue
        for bar in sorted(bars, key=lambda c: timestamp(c["candle_close_at"])):
            price = float(bar["close"])
            if side*(price-float(row["stop_loss"])) <= 0:
                result, at = "STOPPED", bar["candle_close_at"]
                break
            target = row["take_profit_1"] if row.get("tp1_closes_position") else row["take_profit_2"]
            if side*(price-float(target)) >= 0:
                result, at = "TP1_HIT" if row.get("tp1_closes_position") else "TP2_HIT", bar["candle_close_at"]
                break
        evidence["provider_results"][provider] = dict(status=result, at=at)
        # A sparse terminal close cannot rule out an earlier opposing exit.
        # Weekend/session gaps require an explicit calendar-aware coverage proof;
        # do not infer one from a provider name or the terminal row.
        cursor = activated
        for bar in sorted(bars, key=lambda c: timestamp(c["candle_close_at"])):
            opened = timestamp(bar.get("candle_open_at"))
            closed = timestamp(bar["candle_close_at"])
            if opened is None or opened > cursor or closed <= opened:
                evidence["flags"].append("PROVIDER_COVERAGE_NOT_ESTABLISHED")
                break
            cursor = max(cursor, closed)
            if at and closed >= timestamp(at):
                break
    states = {v["status"] for v in evidence["provider_results"].values()}
    if len(states) > 1:
        evidence["flags"].append("PROVIDER_DISAGREEMENT")
    if not groups or "UNKNOWN" in groups:
        evidence["flags"].append("MISSING_PROVIDER_EVIDENCE")
    stored = row.get("status")
    if stored in {"STOPPED", "TP1_HIT", "TP2_HIT"} and states and stored not in states:
        evidence["flags"].append("STORED_OUTCOME_NOT_SUPPORTED")
    evidence["unresolved"] = bool(evidence["flags"])
    return evidence
