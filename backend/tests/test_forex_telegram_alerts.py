import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

from app.forex.models import ForexSignal
from app.services.alert_dedupe import mark_forex_alert_sent, should_skip_forex_alert
from bot.forex_alerts import evaluate_forex_result, forex_signal_id, run_forex_alert_scan
from bot.formatter import format_forex_alert, pip_distance
from bot.keyboards import forex_alert_keyboard
from bot.storage import add_subscriber, get_subscribers, set_subscriber_preference


def forex_signal(**overrides) -> ForexSignal:
    payload = {
        "pair": "EURUSD",
        "direction": "LONG",
        "score": 84,
        "grade": "A",
        "session": "London-New York overlap",
        "pre_session_bias": "Bullish 4H + bullish 1H structure",
        "entry": 1.0845,
        "stopLoss": 1.0812,
        "tp1": 1.0881,
        "tp2": 1.0915,
        "rr": 2.12,
        "spreadStatus": "SAFE",
        "newsRisk": "LOW",
        "reason": "London overlap breakout with bullish continuation confirmation.",
        "lastUpdated": datetime(2026, 7, 13, 12, 0, tzinfo=UTC),
        "status": "active",
        "timeframe": "15m",
        "provider": "twelvedata",
    }
    payload.update(overrides)
    return ForexSignal(**payload)


def test_forex_message_formatting_includes_required_fields_and_pips():
    signal = forex_signal()
    payload = signal.model_dump(mode="json")
    payload["signal_id"] = forex_signal_id(signal)

    message = format_forex_alert(payload)

    for expected in (
        "SWIFTCHART FOREX",
        "Pair: EURUSD",
        "Signal: Buy",
        "Timeframe: 15M",
        "Score: 84/100 | Grade: A",
        "Entry Range: 1.0843 — 1.0847",
        "Stop Loss: 1.0812 (33.0 pips)",
        "TP1: 1.0881 (36.0 pips)",
        "TP2: 1.0915 (70.0 pips)",
        "R:R: 2.12",
        "Session: London-New York overlap",
        "Setup: London overlap breakout",
        f"Signal ID: {payload['signal_id']}",
    ):
        assert expected in message


def test_normal_forex_pip_calculation():
    assert pip_distance("GBPUSD", 1.2700, 1.2675) == 25.0


def test_jpy_pair_pip_calculation():
    assert pip_distance("USDJPY", 150.25, 149.95) == 30.0


def test_forex_duplicate_prevention_uses_existing_state_file(monkeypatch, tmp_path):
    monkeypatch.setenv("ALERT_DEDUPE_STATE_PATH", str(tmp_path / "dedupe.json"))
    signal = forex_signal()

    assert should_skip_forex_alert(signal) is False
    mark_forex_alert_sent(signal)
    assert should_skip_forex_alert(signal) is True


def test_market_preference_filtering(monkeypatch, tmp_path):
    monkeypatch.setenv("BOT_STATE_PATH", str(tmp_path / "bot.json"))
    monkeypatch.delenv("TELEGRAM_ALERT_CHAT_IDS", raising=False)
    add_subscriber(101, "crypto")
    add_subscriber(202, "forex")
    add_subscriber(303, "both")

    assert get_subscribers("crypto") == {101, 303}
    assert get_subscribers("forex") == {202, 303}

    set_subscriber_preference(101, "forex")
    assert get_subscribers("forex") == {101, 202, 303}


def test_forex_telegram_button_payloads():
    signal_id = "swiftchart-fx-test123"
    keyboard = forex_alert_keyboard(signal_id)
    buttons = [button for row in keyboard.inline_keyboard for button in row]

    assert [(button.text, button.callback_data) for button in buttons] == [
        ("View Analysis", f"analysis:{signal_id}"),
        ("Open in MT5", f"mt5:{signal_id}"),
        ("Auto Trade", f"autotrade:{signal_id}"),
        ("Copy Setup", f"copy:{signal_id}"),
    ]


def test_forex_result_levels_are_detected_once_in_order():
    alert = {
        "direction": "LONG",
        "stop_loss": 1.0812,
        "tp1": 1.0881,
        "tp2": 1.0915,
        "result_status": "open",
    }

    assert evaluate_forex_result(alert, high=1.0882, low=1.0830) == "tp1_hit"
    alert["result_status"] = "tp1_hit"
    assert evaluate_forex_result(alert, high=1.0916, low=1.0840) == "tp2_hit"
    assert evaluate_forex_result(alert, high=1.0850, low=1.0810) == "sl_hit"


def test_forex_scan_filters_preferences_and_prevents_duplicate_delivery(monkeypatch, tmp_path):
    import bot.forex_alerts as alerts

    monkeypatch.setenv("BOT_STATE_PATH", str(tmp_path / "bot.json"))
    monkeypatch.setenv("ALERT_DEDUPE_STATE_PATH", str(tmp_path / "dedupe.json"))
    monkeypatch.delenv("TELEGRAM_ALERT_CHAT_IDS", raising=False)
    add_subscriber(101, "crypto")
    add_subscriber(202, "forex")

    async def fake_scan(*args, **kwargs):
        return SimpleNamespace(
            configured=True,
            provider="fake",
            signals=[forex_signal()],
            errors=[],
        )

    async def fake_results(*args, **kwargs):
        return {"checked": 0, "updated": 0, "sent": 0}

    class FakeBot:
        def __init__(self):
            self.chat_ids = []

        async def send_message(self, chat_id, text, reply_markup=None):
            self.chat_ids.append(chat_id)
            return SimpleNamespace(message_id=len(self.chat_ids))

    monkeypatch.setattr(alerts, "scan_forex", fake_scan)
    monkeypatch.setattr(alerts, "check_forex_alert_results", fake_results)
    bot = FakeBot()

    first = asyncio.run(run_forex_alert_scan(bot, provider=object()))
    second = asyncio.run(run_forex_alert_scan(bot, provider=object()))

    assert first["sent"] == 1
    assert second["sent"] == 0
    assert bot.chat_ids == [202]
