from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

import pandas as pd

from app.forex.config import ForexPairConfig
from app.forex.models import ForexChartCandle, ForexChartLevels, ForexChartZone, ForexPairChartResponse
from app.forex.scanner import _atr, _structure, _trend


DISPLAY_NAMES = {
    "EUR": "Euro", "GBP": "British Pound", "USD": "US Dollar", "JPY": "Japanese Yen",
    "AUD": "Australian Dollar", "CAD": "Canadian Dollar", "CHF": "Swiss Franc",
    "NZD": "New Zealand Dollar", "XAU": "Gold",
}
ZoneSide = Literal["DEMAND", "SUPPLY"]
TERMINAL_STATES = {"BROKEN", "INVALIDATED", "EXPIRED"}
ACTIONABLE_STATES = {
    "FRESH", "ACTIVE", "TOUCHED", "SWEPT", "RECLAIMED", "CONFIRMED",
    "WEAKENED", "ROLE_REVERSED", "RETESTING",
}


def pair_display_name(symbol: str) -> str:
    return f"{DISPLAY_NAMES.get(symbol[:3], symbol[:3])} / {DISPLAY_NAMES.get(symbol[3:], symbol[3:])}"


def pair_chart_higher_timeframe(timeframe: str) -> str | None:
    return {"1H": "4H", "4H": "1D", "1D": None}[timeframe]


def _timestamp(value: Any) -> pd.Timestamp:
    result = pd.Timestamp(value)
    return result.tz_localize(UTC) if result.tzinfo is None else result.tz_convert(UTC)


def _atr_at(frame: pd.DataFrame, index: int, pip_size: float) -> float:
    return max(_atr(frame.iloc[max(0, index - 39):index + 1]), pip_size * 2)


def _zone_state(frame: pd.DataFrame, low: float, high: float, side: str, atr: float) -> str:
    """Compatibility helper; the main engine preserves state from the zone origin."""
    recent = frame.tail(6)
    last = recent.iloc[-1]
    close = float(last["close"])
    completed_before_last = frame.iloc[:-1]
    if side == "buy" and bool((completed_before_last["close"].astype(float) < low - atr * 0.12).any()):
        return "INVALIDATED"
    if side == "sell" and bool((completed_before_last["close"].astype(float) > high + atr * 0.12).any()):
        return "INVALIDATED"
    overlaps = (recent["low"].astype(float) <= high) & (recent["high"].astype(float) >= low)
    touched_before_last = bool(overlaps.iloc[:-1].any())
    touching_now = bool(overlaps.iloc[-1])
    if side == "buy":
        if close < low - atr * 0.12:
            return "INVALIDATED"
        if float(last["low"]) < low and close > high:
            return "RECLAIMED"
        if float(last["low"]) < low and close >= low:
            return "SWEPT"
        if touched_before_last and close > high + atr * 0.3:
            return "CONFIRMED"
    else:
        if close > high + atr * 0.12:
            return "INVALIDATED"
        if float(last["high"]) > high and close < low:
            return "RECLAIMED"
        if float(last["high"]) > high and close <= high:
            return "SWEPT"
        if touched_before_last and close < low - atr * 0.3:
            return "CONFIRMED"
    if touching_now:
        return "RETESTING" if touched_before_last else "TOUCHED"
    return "ACTIVE"


@dataclass
class ZoneRecord:
    zone_id: str
    symbol: str
    timeframe: str
    side: ZoneSide
    low: float
    high: float
    created_index: int
    created_at: pd.Timestamp
    origin_index: int
    origin_at: pd.Timestamp
    origin_atr: float
    base_strength: float
    displacement_atr: float
    prominence_atr: float
    state: str = "FRESH"
    strength: float = 0
    touch_count: int = 0
    successful_reactions: int = 0
    failed_reactions: int = 0
    role_reversed_from: str | None = None
    suppressed_by: str | None = None
    lineage_depth: int = 0
    last_touch_index: int | None = None
    last_touch_at: pd.Timestamp | None = None
    last_exit_index: int | None = None
    last_exit_at: pd.Timestamp | None = None
    state_changed_at: pd.Timestamp | None = None
    interaction_active: bool = False
    consecutive_break_closes: int = 0
    history: list[dict[str, Any]] = field(default_factory=list)

    def transition(self, state: str, timestamp: pd.Timestamp, reason: str) -> None:
        if self.state == state:
            return
        self.state = state
        self.state_changed_at = timestamp
        self.history.append({"timestamp": timestamp.to_pydatetime(), "state": state, "reason": reason})

    @property
    def midpoint(self) -> float:
        return (self.low + self.high) / 2


@dataclass
class ZoneReplayMetrics:
    zones_created: int = 0
    zones_invalidated: int = 0
    zones_expired: int = 0
    zones_role_reversed: int = 0
    zone_replacements: int = 0
    zone_identity_changes: int = 0
    lifetimes: list[int] = field(default_factory=list)

    def as_dict(self) -> dict[str, float | int]:
        return {
            "zones_created": self.zones_created,
            "zones_invalidated": self.zones_invalidated,
            "zones_expired": self.zones_expired,
            "zones_role_reversed": self.zones_role_reversed,
            "zone_replacements": self.zone_replacements,
            "zone_identity_changes": self.zone_identity_changes,
            "average_zone_lifetime_bars": round(sum(self.lifetimes) / len(self.lifetimes), 2) if self.lifetimes else 0,
        }


class StatefulZoneEngine:
    """Deterministic completed-candle lifecycle replay with stable zone identity.

    Five-candle swings become FRESH only after displacement and prominence pass
    ATR-normalized significance gates. Price must leave by 0.25 ATR before a
    later return counts as a new touch. A wick can produce SWEPT but never a
    break. BROKEN requires a completed close 0.12 ATR beyond the boundary and a
    candle body of at least 0.25 ATR. A broken original creates one linked
    ROLE_REVERSED zone; a failed reversal is INVALIDATED rather than reversed
    again. Three independent tests WEAKEN a zone, and distant zones untouched
    for 80 bars EXPIRE after 160 bars.
    """

    def __init__(self, symbol: str, timeframe: str, pip_size: float) -> None:
        self.symbol = symbol
        self.timeframe = timeframe
        self.pip_size = pip_size
        self.zones: dict[str, ZoneRecord] = {}
        self.primary_ids: dict[ZoneSide, str | None] = {"DEMAND": None, "SUPPLY": None}
        self.metrics = ZoneReplayMetrics()
        self.primary_history: list[dict[str, Any]] = []
        self.break_history: list[dict[str, Any]] = []

    def _zone_id(self, side: ZoneSide, origin_at: pd.Timestamp, role_parent: str | None = None) -> str:
        base = f"{self.symbol.lower()}_{self.timeframe.lower()}_{side.lower()}_{origin_at.strftime('%Y%m%dT%H%M')}"
        return f"{base}_rr" if role_parent else base

    def _set_strength(self, zone: ZoneRecord, index: int) -> None:
        age_penalty = max(0.0, (index - zone.created_index - 60) * 0.10)
        touch_penalty = max(0, zone.touch_count - 1) * 9.0
        zone.strength = round(max(0.0, min(
            100.0,
            zone.base_strength - age_penalty - touch_penalty - zone.failed_reactions * 12.0
            + min(zone.successful_reactions, 2) * 3.0 + (5.0 if zone.role_reversed_from else 0.0),
        )), 1)

    def _candidate(self, frame: pd.DataFrame, index: int, side: ZoneSide) -> ZoneRecord | None:
        origin_index = index - 2
        if origin_index < 2:
            return None
        window = frame.iloc[origin_index - 2:origin_index + 3]
        origin = frame.iloc[origin_index]
        atr = _atr_at(frame, origin_index, self.pip_size)
        if side == "DEMAND":
            pivot = float(origin["low"])
            if pivot != float(window["low"].min()):
                return None
            prominence = max(0.0, float(window.drop(window.index[2])["low"].astype(float).min()) - pivot) / atr
            high = max(min(max(float(origin["open"]), float(origin["close"])), pivot + atr * 0.45), pivot + atr * 0.12)
            low = pivot
            displacement = (float(frame.iloc[origin_index:index + 1]["high"].max()) - high) / atr
        else:
            pivot = float(origin["high"])
            if pivot != float(window["high"].max()):
                return None
            prominence = max(0.0, pivot - float(window.drop(window.index[2])["high"].astype(float).max())) / atr
            low = min(max(min(float(origin["open"]), float(origin["close"])), pivot - atr * 0.45), pivot - atr * 0.12)
            high = pivot
            displacement = (low - float(frame.iloc[origin_index:index + 1]["low"].min())) / atr
        if displacement < 0.55 or (prominence < 0.015 and displacement < 0.62):
            return None
        origin_at = _timestamp(origin["timestamp"])
        score = min(92.0, 52 + min(displacement, 2.0) * 13 + min(prominence, 0.6) * 20)
        zone = ZoneRecord(
            zone_id=self._zone_id(side, origin_at), symbol=self.symbol, timeframe=self.timeframe,
            side=side, low=low, high=high, created_index=index,
            created_at=_timestamp(frame.iloc[index]["timestamp"]), origin_index=origin_index,
            origin_at=origin_at, origin_atr=atr, base_strength=score,
            displacement_atr=displacement, prominence_atr=prominence, strength=round(score, 1),
        )
        zone.history.append({
            "timestamp": zone.created_at.to_pydatetime(), "state": "FRESH",
            "reason": "Confirmed five-candle swing with structural displacement.",
        })
        return zone

    def _overlaps_materially(self, first: ZoneRecord, second: ZoneRecord) -> bool:
        tolerance = max(first.origin_atr, second.origin_atr) * 0.30
        return first.low <= second.high + tolerance and second.low <= first.high + tolerance

    def _add_zone(self, candidate: ZoneRecord) -> bool:
        if candidate.zone_id in self.zones:
            return False
        peers = [
            zone for zone in self.zones.values()
            if zone.side == candidate.side and zone.state in ACTIONABLE_STATES
            and not self._suppression_active(zone)
            and self._overlaps_materially(zone, candidate)
        ]
        if not candidate.role_reversed_from and peers:
            blocker = max(peers, key=lambda peer: peer.strength)
            if blocker.strength >= candidate.strength - 8:
                # Keep a structurally valid overlapping candidate dormant rather
                # than deleting it. It continues through its own lifecycle and
                # becomes selectable only if the stronger blocker is terminal.
                candidate.suppressed_by = blocker.zone_id
        self.zones[candidate.zone_id] = candidate
        self.metrics.zones_created += 1
        return True

    def _suppression_active(self, zone: ZoneRecord) -> bool:
        if not zone.suppressed_by:
            return False
        blocker = self.zones.get(zone.suppressed_by)
        if blocker is None or blocker.state in TERMINAL_STATES:
            return False
        return not self._suppression_active(blocker)

    def _reverse(self, zone: ZoneRecord, index: int, timestamp: pd.Timestamp) -> ZoneRecord | None:
        if zone.role_reversed_from or zone.lineage_depth >= 1:
            return None
        side: ZoneSide = "SUPPLY" if zone.side == "DEMAND" else "DEMAND"
        reversed_zone = ZoneRecord(
            zone_id=self._zone_id(side, zone.origin_at, zone.zone_id), symbol=zone.symbol,
            timeframe=zone.timeframe, side=side, low=zone.low, high=zone.high,
            created_index=index, created_at=timestamp, origin_index=zone.origin_index,
            origin_at=zone.origin_at, origin_atr=zone.origin_atr,
            base_strength=max(45.0, zone.strength * 0.88),
            displacement_atr=zone.displacement_atr, prominence_atr=zone.prominence_atr,
            state="ROLE_REVERSED", strength=max(45.0, round(zone.strength * 0.88, 1)),
            role_reversed_from=zone.zone_id, lineage_depth=zone.lineage_depth + 1,
            last_exit_index=index, last_exit_at=timestamp, state_changed_at=timestamp,
        )
        reversed_zone.history.append({
            "timestamp": timestamp.to_pydatetime(), "state": "ROLE_REVERSED",
            "reason": f"Created from completed structural break of {zone.zone_id}.",
        })
        if self._add_zone(reversed_zone):
            self.metrics.zones_role_reversed += 1
            return reversed_zone
        return None

    def _breaks(self, zone: ZoneRecord, candle: pd.Series, atr: float) -> bool:
        close = float(candle["close"])
        beyond = close <= zone.low - atr * 0.12 if zone.side == "DEMAND" else close >= zone.high + atr * 0.12
        zone.consecutive_break_closes = zone.consecutive_break_closes + 1 if beyond else 0
        if not beyond:
            return False
        body_confirms = abs(close - float(candle["open"])) >= atr * 0.25
        return body_confirms or zone.consecutive_break_closes >= 2

    def _favorable_exit(self, zone: ZoneRecord, close: float, atr: float) -> bool:
        return close >= zone.high + atr * 0.25 if zone.side == "DEMAND" else close <= zone.low - atr * 0.25

    def _process_zone(self, zone: ZoneRecord, candle: pd.Series, index: int, atr: float) -> None:
        if zone.state in TERMINAL_STATES or index <= zone.created_index:
            return
        timestamp = _timestamp(candle["timestamp"])
        close, low, high = (float(candle[key]) for key in ("close", "low", "high"))
        age = index - zone.created_index
        last_interaction = zone.last_touch_index if zone.last_touch_index is not None else zone.created_index
        if age > 160 and index - last_interaction > 80 and abs(close - zone.midpoint) / max(atr, self.pip_size) > 4:
            zone.transition("EXPIRED", timestamp, "Untouched for 80 bars, older than 160 bars, and over 4 ATR away.")
            self.metrics.zones_expired += 1
            self.metrics.lifetimes.append(age)
            return
        if self._breaks(zone, candle, atr):
            zone.failed_reactions += 1
            if self._suppression_active(zone):
                zone.transition("INVALIDATED", timestamp, "Dormant overlapping candidate failed before becoming eligible.")
                self.metrics.zones_invalidated += 1
            elif zone.role_reversed_from:
                zone.transition("INVALIDATED", timestamp, "Role reversal failed on a completed structural close.")
                self.metrics.zones_invalidated += 1
            else:
                zone.transition("BROKEN", timestamp, "Completed close and candle body broke the zone by the ATR threshold.")
                direction = "BEARISH" if zone.side == "DEMAND" else "BULLISH"
                self.break_history.append({
                    "timestamp": timestamp, "direction": direction, "zone_id": zone.zone_id,
                    "low": zone.low, "high": zone.high,
                })
                self._reverse(zone, index, timestamp)
            self.metrics.lifetimes.append(age)
            self._set_strength(zone, index)
            return
        overlap = low <= zone.high and high >= zone.low
        favorable_exit = self._favorable_exit(zone, close, atr)
        prior_state = zone.state
        if overlap and not zone.interaction_active and zone.last_exit_index is not None and index > zone.last_exit_index:
            zone.interaction_active = True
            zone.touch_count += 1
            zone.last_touch_index = index
            zone.last_touch_at = timestamp
            zone.transition(
                "TOUCHED" if zone.touch_count == 1 else "RETESTING", timestamp,
                "Independent revisit after price materially exited the zone.",
            )
        swept = (
            zone.side == "DEMAND" and low < zone.low and close >= zone.low
        ) or (
            zone.side == "SUPPLY" and high > zone.high and close <= zone.high
        )
        reclaimed_now = False
        if swept:
            zone.transition("SWEPT", timestamp, "Wick crossed the boundary but the completed candle did not structurally break it.")
        elif prior_state == "SWEPT":
            reclaimed_now = (
                zone.side == "DEMAND" and close >= zone.high + atr * 0.12
            ) or (
                zone.side == "SUPPLY" and close <= zone.low - atr * 0.12
            )
            if reclaimed_now:
                zone.transition("RECLAIMED", timestamp, "Completed candle reclaimed the far edge by at least 0.12 ATR.")
        if favorable_exit:
            if zone.interaction_active:
                zone.interaction_active = False
                zone.last_exit_index = index
                zone.last_exit_at = timestamp
            if zone.state == "FRESH":
                zone.last_exit_index = index
                zone.last_exit_at = timestamp
                zone.transition("ACTIVE", timestamp, "Price displaced at least 0.25 ATR away from the fresh zone.")
            elif zone.state in {"TOUCHED", "RETESTING", "SWEPT", "RECLAIMED", "WEAKENED"} and not reclaimed_now:
                zone.successful_reactions += 1
                zone.transition("CONFIRMED", timestamp, "Completed candle confirmed a favorable reaction at least 0.25 ATR away.")
        if zone.touch_count >= 3 and zone.state not in TERMINAL_STATES:
            zone.transition("WEAKENED", timestamp, "Three independent tests indicate likely order consumption.")
        self._set_strength(zone, index)

    def _rank(self, zone: ZoneRecord, price: float, atr: float, htf_context: str) -> float:
        distance_penalty = min(24.0, abs(price - zone.midpoint) / max(atr, self.pip_size) * 2.5)
        alignment = 0.0
        if htf_context == "BULLISH":
            alignment = 6.0 if zone.side == "DEMAND" else -3.0
        elif htf_context == "BEARISH":
            alignment = 6.0 if zone.side == "SUPPLY" else -3.0
        return zone.strength + alignment + (10.0 if zone.role_reversed_from else 0.0) - distance_penalty

    def _select_primary(self, side: ZoneSide, price: float, atr: float, htf_context: str) -> ZoneRecord | None:
        candidates = [
            zone for zone in self.zones.values()
            if zone.side == side and zone.state in ACTIONABLE_STATES and zone.strength >= 40
            and not self._suppression_active(zone)
            and (zone.low <= price + atr * 0.15 if side == "DEMAND" else zone.high >= price - atr * 0.15)
        ]
        if not candidates:
            self.primary_ids[side] = None
            return None
        best = max(candidates, key=lambda item: self._rank(item, price, atr, htf_context))
        current = self.zones.get(self.primary_ids[side] or "")
        if current not in candidates:
            selected = best
        elif (
            best.zone_id != current.zone_id
            and self._rank(best, price, atr, htf_context)
            >= self._rank(current, price, atr, htf_context) + (5 if best.role_reversed_from else 10)
        ):
            selected = best
            self.metrics.zone_replacements += 1
        else:
            selected = current
        if selected.zone_id != self.primary_ids[side]:
            if self.primary_ids[side] is not None:
                self.metrics.zone_identity_changes += 1
            self.primary_ids[side] = selected.zone_id
        return selected

    def run(self, frame: pd.DataFrame, htf_context: str = "UNAVAILABLE") -> tuple[ZoneRecord | None, ZoneRecord | None]:
        working = frame.sort_values("timestamp").reset_index(drop=True)
        for index in range(len(working)):
            candle = working.iloc[index]
            atr = _atr_at(working, index, self.pip_size)
            for zone in list(self.zones.values()):
                self._process_zone(zone, candle, index, atr)
            if index >= 4:
                for side in ("DEMAND", "SUPPLY"):
                    candidate = self._candidate(working, index, side)
                    if candidate:
                        self._add_zone(candidate)
            price = float(candle["close"])
            demand = self._select_primary("DEMAND", price, atr, htf_context)
            supply = self._select_primary("SUPPLY", price, atr, htf_context)
            signature = (demand.zone_id if demand else None, supply.zone_id if supply else None)
            if not self.primary_history or signature != self.primary_history[-1]["signature"]:
                self.primary_history.append({"index": index, "timestamp": _timestamp(candle["timestamp"]), "signature": signature})
        price = float(working.iloc[-1]["close"])
        atr = _atr_at(working, len(working) - 1, self.pip_size)
        return self._select_primary("DEMAND", price, atr, htf_context), self._select_primary("SUPPLY", price, atr, htf_context)

    def latest_break(self) -> dict[str, Any] | None:
        return self.break_history[-1] if self.break_history else None


def _htf_context(higher_frame: pd.DataFrame | None) -> str:
    if higher_frame is None or len(higher_frame) < 25:
        return "UNAVAILABLE"
    return {"bullish": "BULLISH", "bearish": "BEARISH"}.get(
        _trend(higher_frame.sort_values("timestamp").reset_index(drop=True)), "NEUTRAL"
    )


def _resolve_bias(frame: pd.DataFrame, htf_context: str, latest_break: dict[str, Any] | None) -> tuple[str, float, datetime | None, str]:
    local_trend = _trend(frame)
    structure, _ = _structure(frame)
    score = 0.0
    reasons: list[str] = []
    for label, value, weight in (
        ("local trend", local_trend, 1.0), ("local structure", structure, 1.0),
        ("higher timeframe", htf_context.lower(), 0.8),
    ):
        if value == "bullish":
            score += weight; reasons.append(f"{label} bullish")
        elif value == "bearish":
            score -= weight; reasons.append(f"{label} bearish")
    changed_at = None
    if latest_break:
        direction = latest_break["direction"]
        score += 4.4 if direction == "BULLISH" else -4.4
        changed_at = latest_break["timestamp"].to_pydatetime()
        reasons.append(f"latest completed structural break {direction.lower()}")
    bias = "BULLISH" if score >= 1.5 else "BEARISH" if score <= -1.5 else "NEUTRAL"
    confidence = min(0.95, 0.45 + abs(score) * 0.12)
    if htf_context == "UNAVAILABLE":
        reasons.append("higher-timeframe context unavailable")
        confidence = max(0.0, confidence - 0.08)
    return bias, round(confidence, 2), changed_at, "; ".join(reasons)


def _has_remaining_target_space(bias: str, price: float, invalidation: float | None, target_1: float | None) -> bool:
    if invalidation is None or target_1 is None:
        return False
    if bias == "BULLISH":
        risk, reward = price - invalidation, target_1 - price
    elif bias == "BEARISH":
        risk, reward = invalidation - price, price - target_1
    else:
        return False
    return risk > 0 and reward >= risk * (1 - 1e-9)


def _first_target(
    bias: str,
    confirmation: float,
    risk: float,
    buy: ZoneRecord | None,
    sell: ZoneRecord | None,
) -> float:
    if bias == "BULLISH":
        return sell.low if sell and sell.low > confirmation else confirmation + risk
    return buy.high if buy and buy.high < confirmation else confirmation - risk


def _confirmation_is_recent(frame: pd.DataFrame, zone: ZoneRecord | None, max_bars: int = 2) -> bool:
    if zone is None or zone.state_changed_at is None:
        return False
    return int((frame["timestamp"].map(_timestamp) > zone.state_changed_at).sum()) <= max_bars


def _trigger_is_overextended(
    bias: str,
    price: float,
    confirmation: float | None,
    atr: float,
) -> bool:
    if confirmation is None or atr <= 0:
        return False
    extension = price - confirmation if bias == "BULLISH" else confirmation - price
    return extension > atr * 0.5


def _entry_requirements_pass(
    bias: str,
    htf_context: str,
    zone: ZoneRecord | None,
    *,
    confirmation_recent: bool,
    confirmation_crossed: bool,
    overextended: bool,
    target_space_ok: bool,
) -> bool:
    return bool(
        zone
        and zone.state == "CONFIRMED"
        and confirmation_recent
        and confirmation_crossed
        and htf_context == bias
        and not overextended
        and target_space_ok
    )


def _to_chart_zone(zone: ZoneRecord, rank: str) -> ForexChartZone:
    return ForexChartZone(
        id=zone.zone_id, zone_id=zone.zone_id, symbol=zone.symbol, timeframe=zone.timeframe,
        side=zone.side, low=zone.low, high=zone.high, origin_low=zone.low, origin_high=zone.high,
        created_at=zone.created_at.to_pydatetime(), state=zone.state, touch_count=zone.touch_count,
        successful_reactions=zone.successful_reactions, failed_reactions=zone.failed_reactions,
        role_reversed_from=zone.role_reversed_from, strength=zone.strength, rank=rank,
        last_touch_at=zone.last_touch_at.to_pydatetime() if zone.last_touch_at is not None else None,
        last_exit_at=zone.last_exit_at.to_pydatetime() if zone.last_exit_at is not None else None,
        state_changed_at=zone.state_changed_at.to_pydatetime() if zone.state_changed_at is not None else None,
        history=zone.history,
    )


def build_pair_chart_analysis(
    pair: ForexPairConfig,
    timeframe: str,
    frame: pd.DataFrame,
    higher_frame: pd.DataFrame | None = None,
) -> ForexPairChartResponse:
    if len(frame) < 30:
        raise ValueError("At least 30 completed candles are required for pair analysis.")
    frame = frame.sort_values("timestamp").reset_index(drop=True)
    htf_context = _htf_context(higher_frame)
    engine = StatefulZoneEngine(pair.pair, timeframe, pair.pip_size)
    buy, sell = engine.run(frame, htf_context)
    bias, bias_confidence, bias_changed_at, bias_reason = _resolve_bias(frame, htf_context, engine.latest_break())
    price = float(frame.iloc[-1]["close"])
    previous = float(frame.iloc[-2]["close"])
    atr = max(_atr(frame), pair.pip_size * 2)
    recent = frame.tail(60)
    range_low, range_high = float(recent["low"].min()), float(recent["high"].max())
    range_position = (price - range_low) / max(range_high - range_low, atr)
    overextended = range_position > 0.88 or range_position < 0.12
    active_zone = buy if bias == "BULLISH" else sell if bias == "BEARISH" else None
    if bias == "BULLISH" and buy:
        confirmation, invalidation = buy.high + atr * 0.18, buy.low - atr * 0.18
        risk = max(confirmation - invalidation, atr * 0.5)
        target_1 = _first_target(bias, confirmation, risk, buy, sell)
        targets = [target_1, max(confirmation + risk * 1.8, target_1 + risk * 0.5)]
    elif bias == "BEARISH" and sell:
        confirmation, invalidation = sell.low - atr * 0.18, sell.high + atr * 0.18
        risk = max(invalidation - confirmation, atr * 0.5)
        target_1 = _first_target(bias, confirmation, risk, buy, sell)
        targets = [target_1, min(confirmation - risk * 1.8, target_1 - risk * 0.5)]
    else:
        confirmation = invalidation = None
        targets = [None, None]
    confirmation_crossed = bool(
        confirmation is not None
        and ((bias == "BULLISH" and price >= confirmation) or (bias == "BEARISH" and price <= confirmation))
    )
    confirmation_recent = _confirmation_is_recent(frame, active_zone)
    target_space_ok = _has_remaining_target_space(bias, price, invalidation, targets[0])
    trigger_overextended = _trigger_is_overextended(bias, price, confirmation, atr)
    htf_alignment_ok = htf_context == bias
    setup_confirmed = bool(active_zone and active_zone.state == "CONFIRMED")
    entry_requirements_pass = _entry_requirements_pass(
        bias, htf_context, active_zone,
        confirmation_recent=confirmation_recent,
        confirmation_crossed=confirmation_crossed,
        overextended=overextended or trigger_overextended,
        target_space_ok=target_space_ok,
    )
    if overextended or trigger_overextended:
        decision = "NO_TRADE"
    elif setup_confirmed and confirmation_recent and confirmation_crossed and not htf_alignment_ok:
        decision = "WAIT"
    elif setup_confirmed and confirmation_recent and confirmation_crossed and not target_space_ok:
        decision = "NO_TRADE"
    elif entry_requirements_pass:
        decision = "LONG" if bias == "BULLISH" else "SHORT"
    elif active_zone and active_zone.role_reversed_from and active_zone.state in {"ROLE_REVERSED", "TOUCHED", "RETESTING", "SWEPT"}:
        decision = "WAIT_FOR_RETEST"
    elif active_zone and active_zone.state in {"ROLE_REVERSED", "TOUCHED", "RETESTING", "SWEPT"}:
        decision = "WAIT_FOR_RETEST" if bias == "BULLISH" else "WAIT_FOR_REJECTION"
    elif bias == "NEUTRAL":
        decision = "WAIT"
    elif active_zone is None or active_zone.strength < 55:
        decision = "NO_TRADE"
    else:
        decision = "WAIT"
    regime = (
        "BULLISH_PULLBACK" if bias == "BULLISH" and range_position < 0.58 else
        "BULLISH_CONTINUATION" if bias == "BULLISH" else
        "BEARISH_PULLBACK" if bias == "BEARISH" and range_position > 0.42 else
        "BEARISH_CONTINUATION" if bias == "BEARISH" else "RANGE"
    )
    if decision in {"LONG", "SHORT"}:
        summary = f"{bias.title()} structure is confirmed after a completed-candle reaction at the primary zone."
    elif decision in {"WAIT_FOR_RETEST", "WAIT_FOR_REJECTION"}:
        summary = "A structural zone is being retested. Wait for a completed reclaim or rejection before entry."
    elif overextended or trigger_overextended:
        summary = "Price is extended near the edge of its recent range. Target space is limited, so SwiftChart is not chasing the move."
    elif setup_confirmed and confirmation_recent and confirmation_crossed and not htf_alignment_ok:
        summary = "The completed-candle setup is not aligned with the higher timeframe, so SwiftChart is waiting."
    elif setup_confirmed and confirmation_recent and confirmation_crossed and not target_space_ok:
        summary = "The setup confirmed, but less than one unit of reward remains to TP1 relative to the current invalidation risk."
    elif bias == "NEUTRAL":
        summary = "Local structure is not sufficiently aligned. Wait for a clean completed-candle break and retest."
    else:
        summary = f"{bias.title()} structure is present, but no completed-candle entry confirmation is available."
    candles = [
        ForexChartCandle(
            timestamp=_timestamp(row.timestamp).to_pydatetime(), open=float(row.open), high=float(row.high),
            low=float(row.low), close=float(row.close), volume=float(getattr(row, "volume", 0) or 0),
        ) for row in frame.itertuples()
    ]
    return ForexPairChartResponse(
        symbol=pair.pair, display_name=pair_display_name(pair.pair), timeframe=timeframe,
        bias=bias, bias_confidence=bias_confidence, bias_changed_at=bias_changed_at,
        bias_reason=bias_reason, htf_context=htf_context, regime=regime, decision=decision,
        current_price=price, change_percentage=round((price - previous) / previous * 100, 3) if previous else 0,
        buy_zones=[_to_chart_zone(buy, "PRIMARY")] if buy else [],
        sell_zones=[_to_chart_zone(sell, "PRIMARY")] if sell else [],
        levels=ForexChartLevels(
            confirmation=confirmation, retest=active_zone.midpoint if active_zone else None,
            invalidation=invalidation, target_1=targets[0], target_2=targets[1],
        ),
        stability={**engine.metrics.as_dict(), "zone_signature_changes": max(0, len(engine.primary_history) - 1)},
        summary=summary, candles=candles, updated_at=datetime.now(UTC),
    )
