from datetime import UTC, datetime, timedelta

import pandas as pd

from app.models.schemas import MarketRegimeSnapshot, RiskSettings, Zone
from app.strategy.bounce_long import detect_bounce_long
from bot.formatter import format_outcome_event, format_trade_alert
from app.config import Settings, get_settings


def _hype_fixture(*, forming=False):
    start = datetime(2026, 11, 14, tzinfo=UTC)
    rows = []
    for i in range(181):
        rows.append({"timestamp": start + timedelta(hours=i), "open": 60.4, "high": 61.2,
                     "low": 60.2, "close": 60.6, "volume": 100.0})
    rows[130].update(open=60.4, high=60.8, low=59.5, close=60.2)
    rows[170].update(open=59.8, high=60.1, low=56.0, close=59.7, volume=250.0)
    rows[171].update(open=59.7, high=60.4, low=59.6, close=60.0)
    rows[172].update(open=60.0, high=60.8, low=60.5, close=60.5)
    rows[173].update(open=60.5, high=61.8, low=60.2, close=60.7)
    rows[174].update(open=60.7, high=61.0, low=60.0, close=60.8)
    rows[175].update(open=60.8, high=61.4, low=60.2, close=61.0)
    rows[176].update(open=61.0, high=62.0, low=60.4, close=61.9)
    rows[177].update(open=61.9, high=62.0, low=60.8, close=61.1)
    if forming:
        rows.append({"timestamp": datetime(2026, 11, 22, 12, tzinfo=UTC), "open": 61.1, "high": 70,
                     "low": 55, "close": 69, "volume": 10000.0})
    return pd.DataFrame(rows)


def _snapshot():
    return MarketRegimeSnapshot(score=-50, label="Bearish", regime_type="TRENDING_DOWN",
        confidence_score=80, bias="Short bias", long_bias="Counter-trend longs require confirmation",
        short_bias="Prioritize shorts", updated_at=datetime(2026, 11, 22, tzinfo=UTC))


def test_bounce_experiment_flags_are_disabled_by_default():
    settings = Settings(_env_file=None)
    assert settings.bounce_long_enabled is False
    assert settings.bounce_long_allow_chop is False
    assert settings.bounce_long_skip_rsi_filter is False


def _detect(monkeypatch, df):
    monkeypatch.setenv("BOUNCE_LONG_ENABLED", "true")
    get_settings.cache_clear()
    monkeypatch.setattr("app.services.trade_history.bounce_long_kill_switch_tripped", lambda: False)
    monkeypatch.setattr("app.services.trade_history.has_active_short_signal", lambda *args: False)
    return detect_bounce_long(symbol="HYPE", timeframe="1h", exchange="hyperliquid", df=df,
        regime="TRENDING_DOWN", market_regime=_snapshot(), settings=RiskSettings(),
        resistances=[Zone(type="resistance", lower=64.5, upper=65.0, strength=.8, touches=4)],
        as_of=datetime(2026, 11, 22, 12, tzinfo=UTC))


def test_multi_candle_sweep_reclaim_higher_low_break_and_retest(monkeypatch):
    idea = _detect(monkeypatch, _hype_fixture())
    assert idea is not None
    assert idea.setup_family == "bounce_long"
    assert round(idea.entry_zone[0], 4) == 60.9
    assert idea.stop_loss < idea.entry_zone[0]
    assert idea.take_profit_1 == 64.5
    assert idea.strategy_decision == "TRADE"


def test_forming_candle_cannot_create_setup(monkeypatch):
    idea = _detect(monkeypatch, _hype_fixture(forming=True))
    assert idea is not None  # Forming data is ignored; closed retest already exists.
    frame = _hype_fixture().iloc[:176].copy()
    # Remove the completed breakout close, so the forming candle would be the only trigger.
    frame.loc[175, ["high", "low", "close"]] = [61.5, 60.8, 61.4]
    frame = pd.concat([frame, pd.DataFrame([{"timestamp": datetime(2026, 11, 22, 12, tzinfo=UTC),
        "open": 61.4, "high": 70, "low": 55, "close": 69, "volume": 10000.0}])], ignore_index=True)
    assert _detect(monkeypatch, frame) is None


def test_breakout_stages_wait_until_limit_retest(monkeypatch):
    frame = _hype_fixture().iloc[:180].copy()
    frame.loc[177, "close"] = 60.6
    idea = _detect(monkeypatch, frame)
    assert idea is not None
    assert idea.entry_status == "WAIT_FOR_RETEST"
    assert idea.strategy_decision == "WAIT_FOR_RETEST"


def test_active_short_conflict_suppresses_candidate(monkeypatch):
    monkeypatch.setenv("BOUNCE_LONG_ENABLED", "true")
    get_settings.cache_clear()
    monkeypatch.setattr("app.services.trade_history.bounce_long_kill_switch_tripped", lambda: False)
    monkeypatch.setattr("app.services.trade_history.has_active_short_signal", lambda *args: True)
    assert detect_bounce_long(symbol="HYPE", timeframe="1h", exchange="hyperliquid",
        df=_hype_fixture(), regime="TRENDING_DOWN", market_regime=_snapshot(),
        settings=RiskSettings(), resistances=[Zone(type="resistance", lower=64.5,
        upper=65.0, strength=.8, touches=4)], as_of=datetime(2026, 11, 22, 12, tzinfo=UTC)) is None


def test_no_reclaim_or_real_higher_low_is_rejected(monkeypatch):
    no_reclaim = _hype_fixture().iloc[:179].copy()
    for i in range(171, 179):
        no_reclaim.loc[i, ["open", "high", "low", "close"]] = [59.2, 59.4, 58.8, 59.0]
    assert _detect(monkeypatch, no_reclaim) is None

    no_pivot = _hype_fixture().iloc[:178].copy()
    for i, low in zip(range(172, 178), [60.5, 60.4, 60.3, 60.2, 60.1, 60.0]):
        no_pivot.loc[i, "low"] = low
    assert _detect(monkeypatch, no_pivot) is None


def test_reward_risk_floor_rejects_nearby_target(monkeypatch):
    monkeypatch.setenv("BOUNCE_LONG_ENABLED", "true")
    get_settings.cache_clear()
    monkeypatch.setattr("app.strategy.support_resistance.find_support_resistance", lambda _df: ([], []))
    frame = _hype_fixture()
    monkeypatch.setattr("app.services.trade_history.bounce_long_kill_switch_tripped", lambda: False)
    monkeypatch.setattr("app.services.trade_history.has_active_short_signal", lambda *args: False)
    near_resistance = [Zone(type="resistance", lower=61.0, upper=61.1, strength=.5, touches=2)]
    assert detect_bounce_long(symbol="HYPE", timeframe="1h", exchange="hyperliquid", df=frame,
        regime="TRENDING_DOWN", market_regime=_snapshot(), settings=RiskSettings(),
        resistances=near_resistance, as_of=datetime(2026, 11, 22, 12, tzinfo=UTC)) is None


def test_bounce_tags_on_entry_and_existing_tp_outcome_path(monkeypatch):
    idea = _detect(monkeypatch, _hype_fixture())
    assert idea is not None
    bounce_message = format_trade_alert(idea)
    assert bounce_message.startswith("🔵 BOUNCE LONG · HYPE · 1H")
    assert "Counter-trend" in bounce_message and "Plan" in bounce_message
    assert "Price swept prior support" in bounce_message
    assert "85" not in bounce_message
    trend_long = idea.model_copy(update={"setup_family": "trend_continuation", "setup_score": 87.0})
    trend_long_message = format_trade_alert(trend_long)
    assert trend_long_message.startswith("🟢 TREND LONG · HYPE · 1H") and "87" not in trend_long_message
    trend_short = idea.model_copy(update={"setup_family": "trend_continuation", "direction": "Short"})
    assert format_trade_alert(trend_short).startswith("🔴 TREND SHORT · HYPE · 1H")
    message = format_outcome_event({"setup_family": "bounce_long", "event_kind": "TP1",
        "symbol": "HYPE", "direction": "LONG", "price": 64.5, "quantity": .5,
        "entry_zone_low": 60.9, "entry_zone_high": 60.9, "stop_loss": 59.97})
    assert message.startswith("🔵 Bounce long HYPE hit TP1 ✅ +3.9R on half the position (+1.9R banked).")
    message = format_outcome_event({"setup_family": "bounce_long", "event_kind": "TP2",
        "symbol": "HYPE", "direction": "LONG", "price": 64.5})
    assert message.startswith("🔵 Bounce long HYPE hit TP2 ✅")
    trend_tp = format_outcome_event({"setup_family": "trend_continuation", "event_kind": "TP1",
        "symbol": "HYPE", "direction": "SHORT", "price": 64.5})
    assert trend_tp.startswith("🔴 Trend short HYPE hit TP1 ✅")
