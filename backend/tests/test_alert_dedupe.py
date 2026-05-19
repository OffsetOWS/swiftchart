from datetime import UTC, datetime, timedelta

from app.models.schemas import TradeIdea
from app.services.alert_dedupe import alert_dedupe_key, mark_alert_sent, setup_fingerprint, should_skip_alert


def idea(
    *,
    direction: str = "Long",
    candle_time: datetime | None = None,
    entry_zone: tuple[float, float] = (100.123456, 101.123456),
) -> TradeIdea:
    return TradeIdea(
        symbol="btcusdt",
        timeframe="4h",
        exchange="hyperliquid",
        source="hyperliquid",
        direction=direction,
        entry_zone=entry_zone,
        stop_loss=96.123456,
        take_profit_1=110.123456,
        take_profit_2=118.123456,
        risk_reward_ratio=2.5,
        reason="Clean SwiftChart setup.",
        confidence_score=82,
        invalid_condition="Break below support.",
        rank_score=91,
        signal_candle_time=candle_time or datetime(2026, 5, 16, 0, 0, tzinfo=UTC),
    )


def test_alert_dedupe_key_uses_source_symbol_timeframe_direction(monkeypatch, tmp_path):
    monkeypatch.setenv("ALERT_DEDUPE_STATE_PATH", str(tmp_path / "dedupe.json"))

    assert alert_dedupe_key(idea()) == "hyperliquid|BTCUSDT|4h|LONG"


def test_same_4h_candle_and_fingerprint_is_skipped(monkeypatch, tmp_path):
    monkeypatch.setenv("ALERT_DEDUPE_STATE_PATH", str(tmp_path / "dedupe.json"))
    current = idea()

    mark_alert_sent(current, namespace="test", now=datetime(2026, 5, 16, 0, 5, tzinfo=UTC))

    assert should_skip_alert(current, namespace="test", now=datetime(2026, 5, 16, 1, 0, tzinfo=UTC))


def test_meaningful_entry_change_can_alert_within_same_candle(monkeypatch, tmp_path):
    monkeypatch.setenv("ALERT_DEDUPE_STATE_PATH", str(tmp_path / "dedupe.json"))
    original = idea()
    changed = idea(entry_zone=(102.0, 103.0))

    mark_alert_sent(original, namespace="test", now=datetime(2026, 5, 16, 0, 5, tzinfo=UTC))

    assert setup_fingerprint(original) != setup_fingerprint(changed)
    assert not should_skip_alert(changed, namespace="test", now=datetime(2026, 5, 16, 1, 0, tzinfo=UTC))


def test_new_confirmed_4h_candle_can_alert_after_default_delay(monkeypatch, tmp_path):
    monkeypatch.setenv("ALERT_DEDUPE_STATE_PATH", str(tmp_path / "dedupe.json"))
    first = idea(candle_time=datetime(2026, 5, 16, 0, 0, tzinfo=UTC))
    next_candle = idea(candle_time=datetime(2026, 5, 16, 4, 0, tzinfo=UTC))

    mark_alert_sent(first, namespace="test", now=datetime(2026, 5, 16, 0, 5, tzinfo=UTC))

    assert not should_skip_alert(next_candle, namespace="test", now=datetime(2026, 5, 16, 4, 6, tzinfo=UTC))


def test_cooldown_minutes_env_blocks_same_fingerprint(monkeypatch, tmp_path):
    monkeypatch.setenv("ALERT_DEDUPE_STATE_PATH", str(tmp_path / "dedupe.json"))
    monkeypatch.setenv("ALERT_COOLDOWN_MINUTES", "240")
    first = idea(candle_time=datetime(2026, 5, 16, 0, 0, tzinfo=UTC))
    repeated = idea(candle_time=datetime(2026, 5, 16, 4, 0, tzinfo=UTC))

    mark_alert_sent(first, namespace="test", now=datetime(2026, 5, 16, 0, 5, tzinfo=UTC))

    assert should_skip_alert(repeated, namespace="test", now=datetime(2026, 5, 16, 3, 55, tzinfo=UTC))
