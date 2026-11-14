import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_STATE = {"subscribers": [], "sent_alerts": [], "signals": {}, "latest_signal_id": None}


def state_path() -> Path:
    return Path(os.getenv("BOT_STATE_PATH", ".swiftchart_bot_state.json"))


def _load() -> dict[str, Any]:
    path = state_path()
    if not path.exists():
        return DEFAULT_STATE.copy()
    try:
        data = json.loads(path.read_text())
        return {**DEFAULT_STATE, **data}
    except (OSError, json.JSONDecodeError):
        return DEFAULT_STATE.copy()


def _save(data: dict[str, Any]) -> None:
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True))


def env_chat_ids() -> set[int]:
    raw = os.getenv("TELEGRAM_ALERT_CHAT_IDS", "")
    ids: set[int] = set()
    for item in raw.split(","):
        item = item.strip()
        if item:
            ids.add(int(item))
    return ids


def get_subscribers() -> set[int]:
    data = _load()
    return {int(chat_id) for chat_id in data.get("subscribers", [])} | env_chat_ids()


def add_subscriber(chat_id: int) -> None:
    data = _load()
    subscribers = {int(item) for item in data.get("subscribers", [])}
    subscribers.add(int(chat_id))
    data["subscribers"] = sorted(subscribers)
    _save(data)


def remove_subscriber(chat_id: int) -> None:
    data = _load()
    subscribers = {int(item) for item in data.get("subscribers", [])}
    subscribers.discard(int(chat_id))
    data["subscribers"] = sorted(subscribers)
    _save(data)


def is_alert_sent(alert_key: str) -> bool:
    data = _load()
    return alert_key in set(data.get("sent_alerts", []))


def mark_alert_sent(alert_key: str, max_items: int = 300) -> None:
    data = _load()
    sent = list(dict.fromkeys([*data.get("sent_alerts", []), alert_key]))
    data["sent_alerts"] = sent[-max_items:]
    _save(data)


def save_signal(signal_id: str, payload: dict[str, Any], max_items: int = 500) -> None:
    data = _load()
    signals = dict(data.get("signals", {}))
    signals[signal_id] = {
        **payload,
        "saved_at": payload.get("saved_at") or datetime.now(timezone.utc).isoformat(),
    }
    if len(signals) > max_items:
        signals = dict(list(signals.items())[-max_items:])
    data["signals"] = signals
    data["latest_signal_id"] = signal_id
    _save(data)


def get_signal(signal_id: str) -> dict[str, Any] | None:
    signal = _load().get("signals", {}).get(signal_id)
    return dict(signal) if isinstance(signal, dict) else None


def get_latest_signal(selector: str | None = None) -> dict[str, Any] | None:
    data = _load()
    stored_signals = data.get("signals", {})
    if not selector:
        latest_signal_id = data.get("latest_signal_id")
        latest = stored_signals.get(latest_signal_id) if latest_signal_id else None
        if isinstance(latest, dict):
            return dict(latest)
    signals = [dict(value) for value in stored_signals.values() if isinstance(value, dict)]
    if selector:
        normalized = selector.strip().upper()
        signals = [
            signal
            for signal in signals
            if str(signal.get("signal_id", "")).upper() == normalized
            or str(signal.get("pair", "")).upper() == normalized
        ]
    if not signals:
        return None
    return max(signals, key=lambda signal: str(signal.get("saved_at") or ""))
