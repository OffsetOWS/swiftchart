from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Iterable

from app.models.schemas import TradeIdea

logger = logging.getLogger(__name__)

DEFAULT_STATE = {"alert_dedupe": {}}
RECENT_FINGERPRINT_LIMIT = 1000


def _state_path() -> Path:
    return Path(os.getenv("ALERT_DEDUPE_STATE_PATH") or os.getenv("BOT_STATE_PATH", ".swiftchart_bot_state.json"))


def _load() -> dict:
    path = _state_path()
    if not path.exists():
        return {"alert_dedupe": {}}
    try:
        data = json.loads(path.read_text())
        return {"alert_dedupe": {}, **data}
    except (OSError, json.JSONDecodeError):
        return {"alert_dedupe": {}}


def _save(data: dict) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True))


def _parse_dt(value: str | datetime | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except ValueError:
        return None


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    normalized = value if value.tzinfo else value.replace(tzinfo=UTC)
    return normalized.astimezone(UTC).replace(microsecond=0).isoformat()


def _source(idea: TradeIdea) -> str:
    return (idea.source or idea.exchange).lower()


def alert_dedupe_key(idea: TradeIdea) -> str:
    entry_low, entry_high = idea.entry_zone
    return "|".join(
        [
            _source(idea),
            idea.symbol.upper(),
            idea.timeframe.lower(),
            idea.direction.upper(),
            _rounded_price(entry_low),
            _rounded_price(entry_high),
            _rounded_price(idea.stop_loss),
            _rounded_price(idea.take_profit_1),
            _rounded_price(idea.take_profit_2),
        ]
    )


def _symbol_direction_key(idea: TradeIdea) -> str:
    return "|".join(
        [
            _source(idea),
            idea.symbol.upper(),
            idea.timeframe.lower(),
            idea.direction.upper(),
        ]
    )


def _price_precision(value: float) -> int:
    absolute = abs(float(value))
    if absolute >= 1000:
        return 2
    if absolute >= 1:
        return 4
    return 6


def _rounded_price(value: float) -> str:
    precision = _price_precision(value)
    return f"{round(float(value), precision):.{precision}f}"


def _setup_shape_fingerprint(idea: TradeIdea) -> str:
    return alert_dedupe_key(idea)


def _legacy_setup_shape_fingerprint(idea: TradeIdea) -> str:
    entry_low, entry_high = idea.entry_zone
    return "|".join(
        [
            _symbol_direction_key(idea),
            _rounded_price(entry_low),
            _rounded_price(entry_high),
            _rounded_price(idea.stop_loss),
            _rounded_price(idea.take_profit_1),
        ]
    )


def setup_fingerprint(idea: TradeIdea) -> str:
    return _setup_shape_fingerprint(idea)


def telegram_alert_cooldown_hours() -> float:
    return max(0.0, float(os.getenv("TELEGRAM_ALERT_COOLDOWN_HOURS", "12")))


def alert_cooldown_minutes(timeframe: str, *, namespace: str = "alerts") -> int:
    if namespace == "telegram":
        return int(telegram_alert_cooldown_hours() * 60)
    default = "60"
    return max(0, int(os.getenv("ALERT_COOLDOWN_MINUTES", default)))


def _namespace(data: dict, namespace: str) -> dict:
    dedupe = data.setdefault("alert_dedupe", {})
    return dedupe.setdefault(namespace, {"keys": {}, "fingerprints": {}})


def _latest_candle_time(idea: TradeIdea) -> datetime | None:
    return _parse_dt(idea.signal_candle_time)


def _cooldown_remaining(current_time: datetime, last_alert_time: datetime | None, cooldown: timedelta) -> int:
    if last_alert_time is None:
        return 0
    remaining = cooldown - (current_time - last_alert_time)
    return max(0, int(remaining.total_seconds()))


def _log_skip(idea: TradeIdea, record: dict | None, current_time: datetime, cooldown: timedelta) -> None:
    last_sent_at = (record or {}).get("last_alert_time")
    last_alert_time = _parse_dt(last_sent_at)
    logger.info(
        (
            "duplicate_skipped source=%s symbol=%s timeframe=%s direction=%s "
            "dedup_key=%s last_sent_at=%s cooldown_remaining=%s"
        ),
        _source(idea),
        idea.symbol.upper(),
        idea.timeframe.lower(),
        idea.direction.upper(),
        alert_dedupe_key(idea),
        last_sent_at,
        _cooldown_remaining(current_time, last_alert_time, cooldown),
    )


def should_skip_alert(idea: TradeIdea, *, namespace: str = "alerts", now: datetime | None = None) -> bool:
    data = _load()
    bucket = _namespace(data, namespace)
    key = _symbol_direction_key(idea)
    dedup_key = alert_dedupe_key(idea)
    record = bucket["keys"].get(key)
    current_time = now or datetime.now(UTC)
    cooldown = timedelta(minutes=alert_cooldown_minutes(idea.timeframe, namespace=namespace))

    if record:
        last_alert_time = _parse_dt(record.get("last_alert_time"))
        legacy_key = _legacy_setup_shape_fingerprint(idea)
        record_fingerprint = str(record.get("fingerprint") or "")
        same_setup = (
            record.get("dedup_key") == dedup_key
            or record.get("fingerprint") == dedup_key
            or record.get("shape_fingerprint") == dedup_key
            or record.get("shape_fingerprint") == legacy_key
            or record_fingerprint.startswith(f"{legacy_key}|")
        )
        cooldown_active = bool(last_alert_time and cooldown.total_seconds() > 0 and current_time - last_alert_time < cooldown)
        previous_closed = str(record.get("status", "active")).upper() in {"CLOSED", "INVALIDATED", "TP_HIT", "SL_HIT"}

        if same_setup and not previous_closed and cooldown_active:
            _log_skip(idea, record, current_time, cooldown)
            return True

    fingerprint_time = _parse_dt(bucket["fingerprints"].get(dedup_key))
    if fingerprint_time and cooldown.total_seconds() > 0 and current_time - fingerprint_time < cooldown:
        _log_skip(idea, record, current_time, cooldown)
        return True

    return False


def mark_alert_sent(idea: TradeIdea, *, namespace: str = "alerts", status: str = "active", now: datetime | None = None) -> None:
    data = _load()
    bucket = _namespace(data, namespace)
    key = _symbol_direction_key(idea)
    dedup_key = alert_dedupe_key(idea)
    current_time = now or datetime.now(UTC)
    bucket["keys"][key] = {
        "source": _source(idea),
        "symbol": idea.symbol.upper(),
        "timeframe": idea.timeframe.lower(),
        "direction": idea.direction.upper(),
        "dedup_key": dedup_key,
        "fingerprint": dedup_key,
        "shape_fingerprint": dedup_key,
        "last_alert_time": _iso(current_time),
        "latest_candle_time": _iso(_latest_candle_time(idea)),
        "status": status,
    }
    fingerprints = bucket["fingerprints"]
    fingerprints[dedup_key] = _iso(current_time)
    if len(fingerprints) > RECENT_FINGERPRINT_LIMIT:
        ordered = sorted(fingerprints.items(), key=lambda item: item[1] or "")
        bucket["fingerprints"] = dict(ordered[-RECENT_FINGERPRINT_LIMIT:])
    _save(data)


def fresh_alerts(ideas: Iterable[TradeIdea], *, namespace: str, mark: bool = False) -> list[TradeIdea]:
    fresh: list[TradeIdea] = []
    for idea in ideas:
        if should_skip_alert(idea, namespace=namespace):
            continue
        fresh.append(idea)
        if mark:
            mark_alert_sent(idea, namespace=namespace)
    return fresh
