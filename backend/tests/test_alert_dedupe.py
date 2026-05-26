import json
from datetime import UTC, datetime

from app.models.schemas import TradeIdea
from app.services.alert_dedupe import alert_dedupe_key, mark_alert_sent, setup_fingerprint, should_skip_alert


def idea(
    *,
    direction: str = "Long",
    candle_time: datetime | None = None,
    entry_zone: tuple[float, float] = (100.123456, 101.123456),
    stop_loss: float = 96.123456,
    take_profit_1: float = 110.123456,
    take_profit_2: float = 118.123456,
) -> TradeIdea:
    return TradeIdea(
        symbol="btcusdt",
        timeframe="4h",
        exchange="hyperliquid",
        source="hyperliquid",
        direction=direction,
        entry_zone=entry_zone,
        stop_loss=stop_loss,
        take_profit_1=take_profit_1,
        take_profit_2=take_profit_2,
        risk_reward_ratio=2.5,
        reason="Clean SwiftChart setup.",
        confidence_score=82,
        invalid_condition="Break below support.",
        rank_score=91,
        signal_candle_time=candle_time or datetime(2026, 5, 16, 0, 0, tzinfo=UTC),
    )


def test_alert_dedupe_key_uses_material_trade_levels(monkeypatch, tmp_path):
    monkeypatch.setenv("ALERT_DEDUPE_STATE_PATH", str(tmp_path / "dedupe.json"))

    assert alert_dedupe_key(idea()) == "hyperliquid|BTCUSDT|4h|LONG|100.1235|101.1235|96.1235|110.1235|118.1235"


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


def test_new_confirmed_4h_candle_is_blocked_by_default_telegram_cooldown(monkeypatch, tmp_path):
    monkeypatch.setenv("ALERT_DEDUPE_STATE_PATH", str(tmp_path / "dedupe.json"))
    first = idea(candle_time=datetime(2026, 5, 16, 0, 0, tzinfo=UTC))
    next_candle = idea(candle_time=datetime(2026, 5, 16, 4, 0, tzinfo=UTC))

    mark_alert_sent(first, namespace="telegram", now=datetime(2026, 5, 16, 0, 5, tzinfo=UTC))

    assert should_skip_alert(next_candle, namespace="telegram", now=datetime(2026, 5, 16, 4, 6, tzinfo=UTC))


def test_cooldown_minutes_env_blocks_same_fingerprint(monkeypatch, tmp_path):
    monkeypatch.setenv("ALERT_DEDUPE_STATE_PATH", str(tmp_path / "dedupe.json"))
    monkeypatch.setenv("ALERT_COOLDOWN_MINUTES", "240")
    first = idea(candle_time=datetime(2026, 5, 16, 0, 0, tzinfo=UTC))
    repeated = idea(candle_time=datetime(2026, 5, 16, 4, 0, tzinfo=UTC))

    mark_alert_sent(first, namespace="test", now=datetime(2026, 5, 16, 0, 5, tzinfo=UTC))

    assert should_skip_alert(repeated, namespace="test", now=datetime(2026, 5, 16, 3, 55, tzinfo=UTC))


def test_different_direction_allowed_within_telegram_cooldown(monkeypatch, tmp_path):
    monkeypatch.setenv("ALERT_DEDUPE_STATE_PATH", str(tmp_path / "dedupe.json"))
    first = idea(direction="Long")
    changed = idea(direction="Short")

    mark_alert_sent(first, namespace="telegram", now=datetime(2026, 5, 16, 0, 5, tzinfo=UTC))

    assert not should_skip_alert(changed, namespace="telegram", now=datetime(2026, 5, 16, 1, 0, tzinfo=UTC))


def test_materially_changed_trade_allowed_within_telegram_cooldown(monkeypatch, tmp_path):
    monkeypatch.setenv("ALERT_DEDUPE_STATE_PATH", str(tmp_path / "dedupe.json"))
    first = idea()
    changed = idea(stop_loss=94.0, take_profit_1=112.0, take_profit_2=120.0)

    mark_alert_sent(first, namespace="telegram", now=datetime(2026, 5, 16, 0, 5, tzinfo=UTC))

    assert not should_skip_alert(changed, namespace="telegram", now=datetime(2026, 5, 16, 1, 0, tzinfo=UTC))


def test_same_trade_allowed_after_telegram_cooldown(monkeypatch, tmp_path):
    monkeypatch.setenv("ALERT_DEDUPE_STATE_PATH", str(tmp_path / "dedupe.json"))
    monkeypatch.setenv("TELEGRAM_ALERT_COOLDOWN_HOURS", "12")
    current = idea()

    mark_alert_sent(current, namespace="telegram", now=datetime(2026, 5, 16, 0, 5, tzinfo=UTC))

    assert not should_skip_alert(current, namespace="telegram", now=datetime(2026, 5, 16, 12, 6, tzinfo=UTC))


def test_duplicate_skip_logs_dedup_context(monkeypatch, tmp_path, caplog):
    monkeypatch.setenv("ALERT_DEDUPE_STATE_PATH", str(tmp_path / "dedupe.json"))
    current = idea()

    mark_alert_sent(current, namespace="telegram", now=datetime(2026, 5, 16, 0, 5, tzinfo=UTC))

    with caplog.at_level("INFO"):
        assert should_skip_alert(current, namespace="telegram", now=datetime(2026, 5, 16, 1, 0, tzinfo=UTC))

    log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert "duplicate_skipped" in log_text
    assert "dedup_key=hyperliquid|BTCUSDT|4h|LONG" in log_text
    assert "last_sent_at=2026-05-16T00:05:00+00:00" in log_text
    assert "cooldown_remaining=" in log_text


def test_legacy_dedupe_state_still_blocks_same_telegram_setup(monkeypatch, tmp_path):
    state_path = tmp_path / "dedupe.json"
    monkeypatch.setenv("ALERT_DEDUPE_STATE_PATH", str(state_path))
    current = idea()
    legacy_shape = "hyperliquid|BTCUSDT|4h|LONG|100.1235|101.1235|96.1235|110.1235"
    state_path.write_text(
        json.dumps(
            {
                "alert_dedupe": {
                    "telegram": {
                        "keys": {
                            "hyperliquid|BTCUSDT|4h|LONG": {
                                "fingerprint": f"{legacy_shape}|2026-05-16T00:00:00+00:00",
                                "shape_fingerprint": legacy_shape,
                                "last_alert_time": "2026-05-16T00:05:00+00:00",
                                "status": "active",
                            }
                        },
                        "fingerprints": {},
                    }
                }
            }
        )
    )

    assert should_skip_alert(current, namespace="telegram", now=datetime(2026, 5, 16, 1, 0, tzinfo=UTC))
