from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from app.config import get_settings
from app.forex.config import SUPPORTED_FOREX_PAIRS
from app.forex.context import (
    _adjustment,
    _desired_currency_direction,
    _synthetic_usd_state,
    classify_external_market,
    evaluate_cross_market_context,
)
from app.forex import limit_service
from app.forex.limit_lifecycle import advance_limit_opportunity
from app.forex.limit_storage import (
    insert_limit_opportunity,
    limit_strategy_stats,
    list_limit_opportunities,
    queue_limit_dispatches,
    update_limit_opportunity,
)
from app.forex.limit_strategy import detect_liquidity_sweep_fvg_limit
from app.forex.models import ForexCrossMarketContext, MarketContextComponent
from app.forex.telegram import format_forex_limit_opportunity
from app.utils import database

NOW = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)


def context(pair="EURUSD", timeframe="1H", adjustment=0):
    return ForexCrossMarketContext(
        pair=pair,
        timeframe=timeframe,
        total_adjustment=adjustment,
        evaluated_at=NOW,
        explanation="Test context.",
    )


def candles(direction="LONG", *, displacement=True, fvg=True):
    rows = []
    start = NOW - timedelta(hours=59)
    for index in range(57):
        base = 1.1000 + (index % 4) * 0.00005
        high = base + 0.0008
        low = base - 0.0008
        if index == 5:
            high = 1.1080
        if index == 10:
            high = 1.1200
        if index == 6:
            low = 1.0920
        if index == 11:
            low = 1.0800
        rows.append({"timestamp": start + timedelta(hours=index), "open": base, "high": high, "low": low, "close": base + 0.0001, "volume": 100, "complete": True})
    if direction == "LONG":
        rows.extend(
            [
                {"timestamp": start + timedelta(hours=57), "open": 1.0990, "high": 1.1000, "low": 1.0988, "close": 1.0998, "volume": 120, "complete": True},
                {"timestamp": start + timedelta(hours=58), "open": 1.1000, "high": 1.1060, "low": 1.0998, "close": 1.1055 if displacement else 1.1004, "volume": 300, "complete": True},
                {"timestamp": start + timedelta(hours=59), "open": 1.1030, "high": 1.1060, "low": 1.1015 if fvg else 1.0995, "close": 1.1040, "volume": 180, "complete": True},
            ]
        )
    else:
        rows.extend(
            [
                {"timestamp": start + timedelta(hours=57), "open": 1.1010, "high": 1.1013, "low": 1.1000, "close": 1.1002, "volume": 120, "complete": True},
                {"timestamp": start + timedelta(hours=58), "open": 1.1000, "high": 1.1002, "low": 1.0940, "close": 1.0945 if displacement else 1.0996, "volume": 300, "complete": True},
                {"timestamp": start + timedelta(hours=59), "open": 1.0970, "high": 1.0985 if fvg else 1.1005, "low": 1.0940, "close": 1.0960, "volume": 180, "complete": True},
            ]
        )
    return pd.DataFrame(rows)


def opportunity(direction="LONG", **kwargs):
    frame = candles(direction, **kwargs)
    result, reason = detect_liquidity_sweep_fvg_limit(
        SUPPORTED_FOREX_PAIRS["EURUSD"], frame, timeframe="1H",
        htf_bias="BULLISH" if direction == "LONG" else "BEARISH",
        context=context(), now=NOW,
    )
    return result, reason


@pytest.fixture
def limit_db(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'limits.db'}")
    monkeypatch.setenv("FOREX_FVG_MIN_GAP_ATR", "0.05")
    monkeypatch.setenv("FOREX_FVG_DISPLACEMENT_ATR", "0.8")
    get_settings.cache_clear()
    database._INITIALIZED = False
    yield
    get_settings.cache_clear()
    database._INITIALIZED = False


def test_bullish_sweep_displacement_and_fvg_creates_buy_limit(limit_db):
    result, reason = opportunity("LONG")
    assert reason == "Qualified shadow-mode limit opportunity."
    assert result.order_type == "BUY_LIMIT"
    assert result.opportunity_status == "WAIT_FOR_RETEST"
    assert result.entry_price < result.current_price


def test_bearish_sweep_displacement_and_fvg_creates_sell_limit(limit_db):
    result, _ = opportunity("SHORT")
    assert result.order_type == "SELL_LIMIT"
    assert result.opportunity_status == "WAIT_FOR_RETEST"
    assert result.entry_price > result.current_price


def test_sweep_without_displacement_creates_no_limit(limit_db):
    result, reason = opportunity("LONG", displacement=False)
    assert result is None
    assert "displacement" in reason.lower()


def test_displacement_without_fvg_creates_no_limit(limit_db):
    result, reason = opportunity("LONG", fvg=False)
    assert result is None
    assert "fvg" in reason.lower()


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_limit_remains_pending_before_entry_touch(limit_db, direction):
    item, _ = opportunity(direction)
    if direction == "LONG":
        low, high, close = item.entry_price + 0.001, item.entry_price + 0.003, item.entry_price + 0.002
    else:
        low, high, close = item.entry_price - 0.003, item.entry_price - 0.001, item.entry_price - 0.002
    updated = advance_limit_opportunity(item, candle_high=high, candle_low=low, candle_close=close, candle_time=NOW + timedelta(hours=1))
    assert updated.opportunity_status == "WAIT_FOR_RETEST"
    assert updated.fill_time is None


def test_pending_opportunity_becomes_active_only_when_filled(limit_db):
    item, _ = opportunity("LONG")
    updated = advance_limit_opportunity(item, candle_high=item.current_price, candle_low=item.entry_price, candle_close=item.entry_price + 0.0002, candle_time=NOW + timedelta(hours=1))
    assert updated.opportunity_status == "ACTIVE_TRADE"
    assert updated.fill_time is not None


def test_expired_opportunity_never_becomes_active(limit_db):
    item, _ = opportunity("LONG")
    updated = advance_limit_opportunity(item, candle_high=item.current_price, candle_low=item.entry_price, candle_close=item.entry_price, candle_time=item.expiry_time)
    assert updated.opportunity_status == "EXPIRED"
    assert updated.fill_time is None
    assert "no trade" in updated.cancellation_reason.lower()


def test_target_reached_before_entry_cancels(limit_db):
    item, _ = opportunity("LONG")
    updated = advance_limit_opportunity(item, candle_high=item.take_profit_1, candle_low=item.entry_price + 0.0002, candle_close=item.current_price, candle_time=NOW + timedelta(hours=1))
    assert updated.opportunity_status == "TARGET_REACHED_BEFORE_ENTRY"
    assert updated.fill_time is None


def test_invalidated_sweep_cancels_before_entry(limit_db):
    item, _ = opportunity("LONG")
    updated = advance_limit_opportunity(item, candle_high=item.current_price, candle_low=item.sweep_extreme - 0.0001, candle_close=item.sweep_extreme - 0.0001, candle_time=NOW + timedelta(hours=1))
    assert updated.opportunity_status == "INVALIDATED"
    assert updated.fill_time is None


def test_duplicate_scans_reuse_same_opportunity(limit_db):
    item, _ = opportunity("LONG")
    first, created = insert_limit_opportunity(item)
    second, created_again = insert_limit_opportunity(item.model_copy(update={"id": "different"}))
    assert created is True
    assert created_again is False
    assert first.id == second.id
    assert len(list_limit_opportunities()) == 1


def test_telegram_outbox_deduplicates_opportunity_event(limit_db):
    item, _ = opportunity("LONG")
    insert_limit_opportunity(item)
    assert queue_limit_dispatches(item.id, "OPPORTUNITY", ["123"]) == 1
    assert queue_limit_dispatches(item.id, "OPPORTUNITY", ["123"]) == 0


def test_restarting_lifecycle_does_not_duplicate_fill_event(limit_db):
    item, _ = opportunity("LONG")
    filled = advance_limit_opportunity(item, candle_high=item.current_price, candle_low=item.entry_price, candle_close=item.entry_price, candle_time=NOW + timedelta(hours=1))
    repeated = advance_limit_opportunity(filled, candle_high=item.current_price, candle_low=item.entry_price, candle_close=item.entry_price, candle_time=NOW + timedelta(hours=1))
    assert repeated.lifecycle_events == filled.lifecycle_events
    assert sum(event["event"] == "ACTIVE_TRADE" for event in repeated.lifecycle_events) == 1


def test_dxy_alignment_increases_confidence():
    adjustment, status = _adjustment(market_direction="BULLISH", market_state="STRONG_BULLISH", desired_direction=1, strong_positive=6, normal_positive=3, normal_negative=-4, strong_negative=-7)
    assert (adjustment, status) == (6, "STRONG_ALIGNMENT")


def test_dxy_conflict_decreases_confidence():
    adjustment, status = _adjustment(market_direction="BULLISH", market_state="BULLISH", desired_direction=-1, strong_positive=6, normal_positive=3, normal_negative=-4, strong_negative=-7)
    assert (adjustment, status) == (-4, "CONFLICT")


def test_quote_usd_direction_is_inverted():
    assert _desired_currency_direction("EURUSD", "LONG", "USD") == -1
    assert _desired_currency_direction("EURUSD", "SHORT", "USD") == 1


def test_oil_context_applies_to_usdcad_short():
    desired = _desired_currency_direction("USDCAD", "SHORT", "CAD")
    adjustment, status = _adjustment(market_direction="BULLISH", market_state="RALLY", desired_direction=desired, strong_positive=4, normal_positive=2, normal_negative=-3, strong_negative=-5)
    assert desired == 1
    assert (adjustment, status) == (2, "ALIGNED")


def test_oil_direction_for_cad_base_pair():
    assert _desired_currency_direction("CADJPY", "LONG", "CAD") == 1
    assert _desired_currency_direction("CADJPY", "SHORT", "CAD") == -1


def test_oil_has_zero_effect_on_non_cad_pair():
    desired = _desired_currency_direction("EURUSD", "LONG", "CAD")
    adjustment, status = _adjustment(market_direction="BULLISH", market_state="RALLY", desired_direction=desired, strong_positive=4, normal_positive=2, normal_negative=-3, strong_negative=-5)
    assert desired == 0
    assert adjustment == 0
    assert status == "NEUTRAL"


def market_frame(direction="up", incomplete=False):
    rows = []
    for index in range(70):
        price = 100 + index * (0.2 if direction == "up" else -0.2)
        rows.append({"timestamp": NOW - timedelta(hours=69-index), "open": price - 0.1, "high": price + 0.3, "low": price - 0.3, "close": price, "volume": 1, "complete": not (incomplete and index == 69)})
    return pd.DataFrame(rows)


class MissingContextData:
    async def completed_candles(self, *args, **kwargs):
        raise RuntimeError("unavailable")


def test_missing_context_data_does_not_crash(monkeypatch):
    monkeypatch.setenv("FOREX_DXY_CONTEXT_ENABLED", "true")
    get_settings.cache_clear()
    result = asyncio.run(evaluate_cross_market_context("EURUSD", "LONG", "1H", MissingContextData(), now=NOW))
    assert result.total_adjustment == 0
    assert result.usd_context.state == "UNAVAILABLE"


def test_incomplete_context_candles_are_rejected():
    with pytest.raises(ValueError, match="Incomplete"):
        classify_external_market(market_frame(incomplete=True), instrument="DXY", timeframe="1H", now=NOW)


def test_telegram_says_limit_is_not_active_trade(limit_db):
    item, _ = opportunity("LONG")
    message = format_forex_limit_opportunity(item)
    assert "BUY LIMIT" in message
    assert "not an active trade until entry is filled" in message
    assert "Open signal" not in message


def test_unfilled_expiry_is_not_recorded_as_loss(limit_db):
    item, _ = opportunity("LONG")
    expired = advance_limit_opportunity(item, candle_high=item.current_price, candle_low=item.current_price, candle_close=item.current_price, candle_time=item.expiry_time)
    assert expired.opportunity_status == "EXPIRED"
    assert expired.fill_time is None
    assert all(event["event"] != "SL_HIT" for event in expired.lifecycle_events)


def test_context_classifier_uses_multi_candle_trend():
    state, direction, strength, *_ = classify_external_market(market_frame("up"), instrument="DXY", timeframe="1H", now=NOW)
    assert state in {"BULLISH", "STRONG_BULLISH"}
    assert direction == "BULLISH"
    assert strength >= 25


def test_active_trade_excursions_persist_without_duplicate_lifecycle_event(limit_db):
    item, _ = opportunity("LONG")
    filled = advance_limit_opportunity(
        item, candle_high=item.current_price, candle_low=item.entry_price,
        candle_close=item.entry_price, candle_time=NOW + timedelta(hours=1),
    )
    insert_limit_opportunity(filled)
    measured = advance_limit_opportunity(
        filled, candle_high=item.entry_price + 0.0004, candle_low=item.entry_price - 0.0002,
        candle_close=item.entry_price + 0.0001, candle_time=NOW + timedelta(hours=2),
    )
    assert measured.opportunity_status == "ACTIVE_TRADE"
    assert measured.mfe_pips > 0
    assert measured.mae_pips > 0
    assert measured.lifecycle_events == filled.lifecycle_events
    update_limit_opportunity(measured)
    stored = list_limit_opportunities()[0]
    assert stored.mfe_pips == measured.mfe_pips
    assert stored.mae_pips == measured.mae_pips


def test_shadow_stats_keep_expiry_separate_from_losses(limit_db):
    item, _ = opportunity("LONG")
    expired = advance_limit_opportunity(
        item, candle_high=item.current_price, candle_low=item.current_price,
        candle_close=item.current_price, candle_time=item.expiry_time,
    )
    insert_limit_opportunity(expired)
    stats = limit_strategy_stats()
    assert stats["expiry_rate"] == 100.0
    assert stats["sl_rate"] == 0.0
    assert stats["expectancy_including_unfilled_r"] == 0.0
    assert {"by_pair", "by_timeframe", "by_session", "dxy_context", "oil_context"} <= stats.keys()


@pytest.mark.parametrize(
    ("guard", "expected"),
    [
        ({"spread_ok": False}, "CANCELLED"),
        ({"structure_valid": False}, "INVALIDATED"),
        ({"fvg_valid": False}, "INVALIDATED"),
        ({"htf_bias_valid": False}, "INVALIDATED"),
        ({"chase_valid": False}, "MISSED_NO_RETEST"),
    ],
)
def test_pending_safety_guards_cancel_before_fill(limit_db, guard, expected):
    item, _ = opportunity("LONG")
    updated = advance_limit_opportunity(
        item, candle_high=item.current_price, candle_low=item.current_price,
        candle_close=item.current_price, candle_time=NOW + timedelta(hours=1), **guard,
    )
    assert updated.opportunity_status == expected
    assert updated.fill_time is None


def earlier_sweep_candles(candles_after_sweep: int) -> pd.DataFrame:
    frame = candles("LONG")
    displacement_index = len(frame) - 2
    sweep_index = displacement_index - candles_after_sweep
    frame.loc[len(frame) - 3, ["open", "high", "low", "close"]] = [1.0995, 1.1000, 1.0993, 1.0998]
    frame.loc[sweep_index, ["open", "high", "low", "close"]] = [1.0990, 1.1000, 1.0987, 1.0997]
    return frame


@pytest.mark.parametrize("candles_after_sweep", [2, 3])
def test_sweep_can_precede_fvg_candle_one(limit_db, candles_after_sweep):
    result, reason = detect_liquidity_sweep_fvg_limit(
        SUPPORTED_FOREX_PAIRS["EURUSD"], earlier_sweep_candles(candles_after_sweep),
        timeframe="1H", htf_bias="BULLISH", context=context(), now=NOW,
    )
    assert reason == "Qualified shadow-mode limit opportunity."
    assert result.order_type == "BUY_LIMIT"
    assert result.sweep_candle_time == earlier_sweep_candles(candles_after_sweep).iloc[-2-candles_after_sweep]["timestamp"]


def test_sweep_outside_configured_sequence_window_is_rejected(limit_db):
    frame = earlier_sweep_candles(4)
    result, reason = detect_liquidity_sweep_fvg_limit(
        SUPPORTED_FOREX_PAIRS["EURUSD"], frame, timeframe="1H",
        htf_bias="BULLISH", context=context(), now=NOW,
    )
    assert result is None
    assert "within 3 completed candles" in reason


def synthetic_frame(pair: str, *, strength: float = 0.15) -> pd.DataFrame:
    usd_sign = -1 if pair.endswith("USD") else 1
    step = strength * usd_sign
    rows = []
    for index in range(70):
        price = 100 + index * step
        rows.append({
            "timestamp": NOW - timedelta(hours=69-index), "open": price - step / 2,
            "high": price + 0.3, "low": price - 0.3, "close": price,
            "volume": 1, "complete": True,
        })
    return pd.DataFrame(rows)


class SyntheticUsdData:
    async def completed_candles(self, pair, timeframe, **kwargs):
        if pair.pair == "DXY":
            raise RuntimeError("provider DXY unavailable")
        if pair.pair == "WTI":
            raise RuntimeError("WTI not needed")
        return synthetic_frame(pair.pair)


class ProviderDxyData:
    async def completed_candles(self, pair, timeframe, **kwargs):
        return market_frame("up")


def test_provider_dxy_remains_preferred_when_available(monkeypatch):
    monkeypatch.setenv("FOREX_DXY_CONTEXT_ENABLED", "true")
    get_settings.cache_clear()
    result = asyncio.run(
        evaluate_cross_market_context("EURUSD", "SHORT", "1H", ProviderDxyData(), now=NOW)
    )
    assert result.usd_context.source == "PROVIDER_DXY"
    assert "Synthetic USD fallback" not in result.usd_context.explanation


def test_synthetic_usd_fallback_is_used_and_inverts_quote_pairs(monkeypatch):
    monkeypatch.setenv("FOREX_DXY_CONTEXT_ENABLED", "true")
    get_settings.cache_clear()
    result = asyncio.run(
        evaluate_cross_market_context("EURUSD", "LONG", "1H", SyntheticUsdData(), now=NOW)
    )
    assert result.usd_context.source == "SYNTHETIC_USD_BASKET"
    assert result.usd_context.direction == "BULLISH"
    assert result.usd_context.alignment_status in {"CONFLICT", "STRONG_CONFLICT"}
    assert result.total_adjustment < 0
    assert "Synthetic USD fallback used" in result.usd_context.explanation


class OnePairDominatesData:
    async def completed_candles(self, pair, timeframe, **kwargs):
        return synthetic_frame(pair.pair, strength=10 if pair.pair == "USDJPY" else 0)


def test_synthetic_usd_caps_each_equal_weight_component():
    state, direction, strength, _, contributions = asyncio.run(
        _synthetic_usd_state(OnePairDominatesData(), timeframe="1H", now=NOW)
    )
    assert contributions["USDJPY"] == 1
    assert state == "NEUTRAL"
    assert direction == "NEUTRAL"
    assert strength == pytest.approx(100 / 7, abs=0.1)


class IncompleteSyntheticUsdData:
    async def completed_candles(self, pair, timeframe, **kwargs):
        if pair.pair == "DXY":
            raise RuntimeError("provider DXY unavailable")
        frame = synthetic_frame(pair.pair)
        frame.loc[frame.index[-1], "complete"] = False
        return frame


def test_incomplete_synthetic_usd_components_are_not_used(monkeypatch):
    monkeypatch.setenv("FOREX_DXY_CONTEXT_ENABLED", "true")
    get_settings.cache_clear()
    result = asyncio.run(
        evaluate_cross_market_context("EURUSD", "LONG", "1H", IncompleteSyntheticUsdData(), now=NOW)
    )
    assert result.usd_context.source == "UNAVAILABLE"
    assert result.usd_context.confidence_adjustment == 0


def test_shadow_mode_scans_persists_and_suppresses_alerts(limit_db, monkeypatch):
    monkeypatch.setenv("FOREX_LIQUIDITY_FVG_LIMIT_ENABLED", "false")
    monkeypatch.setenv("FOREX_LIQUIDITY_FVG_LIMIT_SHADOW_MODE", "true")
    get_settings.cache_clear()
    monkeypatch.setattr(limit_service, "SUPPORTED_FOREX_PAIRS", {"EURUSD": SUPPORTED_FOREX_PAIRS["EURUSD"]})

    async def completed_candles(*args, **kwargs):
        return candles("LONG")

    monkeypatch.setattr(limit_service.ForexMarketDataService, "completed_candles", completed_candles)
    alerts = []
    monkeypatch.setattr(limit_service, "enqueue_forex_limit_event", lambda *args: alerts.append(args))
    result = asyncio.run(limit_service.scan_limit_opportunities(timeframe="1H", now=NOW))
    assert result["enabled"] is True
    assert result["shadow_mode"] is True
    assert len(result["created"]) == 1
    stored = list_limit_opportunities()[0]
    assert len(list_limit_opportunities()) == 1
    assert alerts == []

    async def fill_candle(*args, **kwargs):
        return pd.DataFrame([{
            "timestamp": NOW + timedelta(hours=1), "open": stored.current_price,
            "high": stored.current_price, "low": stored.entry_price,
            "close": stored.entry_price, "volume": 1, "complete": True,
        }])

    monkeypatch.setattr(limit_service.ForexMarketDataService, "completed_candles", fill_candle)
    lifecycle_updates = asyncio.run(limit_service.update_limit_lifecycle(now=NOW + timedelta(hours=1)))
    assert lifecycle_updates[0].opportunity_status == "ACTIVE_TRADE"
    assert list_limit_opportunities()[0].opportunity_status == "ACTIVE_TRADE"
    assert alerts == []


def test_tp1_partial_position_remains_active_and_full_close_is_terminal(limit_db):
    item, _ = opportunity("LONG")
    filled = advance_limit_opportunity(
        item, candle_high=item.current_price, candle_low=item.entry_price,
        candle_close=item.entry_price, candle_time=NOW + timedelta(hours=1),
    )
    partial = advance_limit_opportunity(
        filled, candle_high=item.take_profit_1, candle_low=item.entry_price,
        candle_close=item.take_profit_1, candle_time=NOW + timedelta(hours=2),
    )
    assert partial.opportunity_status == "TP1_HIT_TP2_RUNNING"
    assert partial.closed_at is None

    full_close = advance_limit_opportunity(
        filled.model_copy(update={"tp1_closes_position": True}),
        candle_high=item.take_profit_1, candle_low=item.entry_price,
        candle_close=item.take_profit_1, candle_time=NOW + timedelta(hours=2),
    )
    assert full_close.opportunity_status == "TP1_HIT"
    assert full_close.closed_at is not None
