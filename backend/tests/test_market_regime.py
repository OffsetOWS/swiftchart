from datetime import UTC, datetime, timedelta

import pandas as pd

from app.models.schemas import MarketRegimeSnapshot
from app.strategy.market_regime import detect_market_regime
from app.models.schemas import RiskSettings, Zone
from app.strategy.trade_ideas import _regime_adjustment, build_trade_ideas


def candles_from_prices(prices: list[float]) -> pd.DataFrame:
    started = datetime(2026, 1, 1, tzinfo=UTC)
    rows = []
    for idx, price in enumerate(prices):
        rows.append(
            {
                "timestamp": started + timedelta(hours=idx),
                "open": price * 0.997,
                "high": price * 1.006,
                "low": price * 0.994,
                "close": price,
                "volume": 1_000 + idx * 5,
            }
        )
    return pd.DataFrame(rows)


def test_market_regime_snapshot_has_structured_transition_fields():
    prices = [100 + idx * 0.08 for idx in range(90)] + [107 + idx * 0.9 for idx in range(30)]
    snapshot = detect_market_regime(candles_from_prices(prices), global_score=30, breadth_above_ma_pct=70)

    assert snapshot.regime_type in {
        "RANGE_BOUND",
        "TRENDING_UP",
        "TRENDING_DOWN",
        "BREAKOUT",
        "BREAKDOWN",
        "CHOP",
        "TRANSITION_TO_BULLISH",
        "TRANSITION_TO_BEARISH",
    }
    assert 0 <= snapshot.confidence_score <= 100
    assert snapshot.confidence_breakdown
    assert snapshot.trade_decision in {"TRADE_ALLOWED", "WAIT", "NO_TRADE"}
    assert "score_delta_12_candles" in snapshot.components
    assert snapshot.explanation


def test_transition_regime_requires_confirmation_before_trading():
    snapshot = MarketRegimeSnapshot(
        score=32,
        label="Transition to Bullish",
        regime_type="TRANSITION_TO_BULLISH",
        confidence_score=62,
        confidence_breakdown={"score_strength": 10},
        structure="Transition To Bullish",
        is_transition=True,
        trade_decision="WAIT",
        bias="Bullish transition",
        long_bias="Wait for bullish confirmation",
        short_bias="Shorts disabled during bullish transition",
        updated_at=datetime.now(UTC),
    )

    adjusted, penalty, note = _regime_adjustment("Long", 82, snapshot, ["price closed above 50 EMA"])
    assert adjusted == 57
    assert penalty == -25
    assert note and "needs 2 confirmations" in note

    adjusted, penalty, note = _regime_adjustment("Short", 82, snapshot, ["bearish market structure break"] * 3)
    assert adjusted == 47
    assert penalty == -35
    assert note and "only long setups" in note


def test_confirmed_bearish_transition_can_score_short_candidate():
    snapshot = MarketRegimeSnapshot(
        score=-32,
        label="Transition to Bearish",
        regime_type="TRANSITION_TO_BEARISH",
        confidence_score=68,
        confidence_breakdown={"bearish": 68},
        structure="LH/LL forming",
        is_transition=True,
        trade_decision="WAIT",
        bias="Bearish transition",
        long_bias="Longs disabled during bearish transition",
        short_bias="Wait for bearish confirmation",
        bias_reason="support break with bearish transition",
        updated_at=datetime.now(UTC),
        components={
            "structural_support_break": True,
            "breakdown_confirmed": True,
            "bearish_ema_momentum": True,
            "bearish_structure_active": False,
            "bullish_structure_active": False,
            "structure_reclaimed_bullish": False,
            "structure_reclaimed_bearish": False,
        },
    )
    adjusted, penalty, note = _regime_adjustment(
        "Short",
        74,
        snapshot,
        ["price closed below support", "bearish momentum confirmation"],
    )

    assert adjusted == 78
    assert penalty == 4
    assert note and "Transition short allowed" in note


def test_transition_to_bearish_builds_short_after_failed_reclaim():
    prices = [110 - idx * 0.18 for idx in range(80)] + [96, 94, 92, 91, 90]
    df = candles_from_prices(prices)
    snapshot = MarketRegimeSnapshot(
        score=-42,
        label="Transition to Bearish",
        regime_type="TRANSITION_TO_BEARISH",
        confidence_score=72,
        confidence_breakdown={"bearish": 72},
        structure="LH/LL forming",
        is_transition=True,
        trade_decision="WAIT",
        bias="Bearish transition",
        long_bias="Longs disabled during bearish transition",
        short_bias="Wait for bearish confirmation",
        bias_reason="support break with bearish transition",
        updated_at=datetime.now(UTC),
        components={
            "structural_support_break": True,
            "breakdown_confirmed": True,
            "bearish_ema_momentum": True,
            "bearish_structure_active": False,
            "bullish_structure_active": False,
            "structure_reclaimed_bullish": False,
            "structure_reclaimed_bearish": False,
        },
    )
    ideas, warning, reviews = build_trade_ideas(
        "TESTUSDT",
        "4h",
        "hyperliquid",
        df,
        Zone(type="support", lower=91, upper=96, strength=0.9, touches=4, strength_score=90),
        Zone(type="resistance", lower=108, upper=112, strength=0.7, touches=3, strength_score=70),
        [],
        RiskSettings(min_rr=0.5),
        "TRANSITION_TO_BEARISH",
        "HTF_BEARISH",
        snapshot,
    )

    assert warning is None
    assert any(idea.direction == "Short" for idea in ideas)
    assert any(review.direction == "Short" and review.accepted for review in reviews)


def test_bearish_bias_switch_on_support_break_lh_ll_and_momentum():
    prices = (
        [100 + idx * 0.45 for idx in range(55)]
        + [125 - idx * 0.25 for idx in range(18)]
        + [120 - idx * 0.7 for idx in range(34)]
    )
    snapshot = detect_market_regime(candles_from_prices(prices), global_score=-15, breadth_above_ma_pct=32)

    assert snapshot.regime_type in {"BREAKDOWN", "TRENDING_DOWN"}
    assert snapshot.bias == "Short bias"
    assert snapshot.components["bearish_structure_active"] is True
    assert snapshot.components["structural_support_break"] is True
    assert snapshot.components["lower_high_lower_low"] is True
    assert snapshot.components["bearish_ema_momentum"] is True
    assert snapshot.bias_flip_trigger
    assert "broke recent structural support" in snapshot.bias_reason


def test_active_bearish_structure_blocks_minor_long_bounces_until_reclaim():
    snapshot = MarketRegimeSnapshot(
        score=-52,
        label="Strong Bearish",
        regime_type="TRENDING_DOWN",
        confidence_score=84,
        confidence_breakdown={"bearish": 84},
        structure="LH/LL",
        is_transition=False,
        trade_decision="TRADE_ALLOWED",
        bias="Short bias",
        long_bias="Counter-trend longs require strong reversal confirmation",
        short_bias="Prioritize shorts",
        bias_reason="price broke recent structural support with LH/LL structure and bearish EMA/momentum confirmation",
        bias_flip_trigger="price broke recent structural support with LH/LL structure and bearish EMA/momentum confirmation",
        updated_at=datetime.now(UTC),
        components={
            "bearish_structure_active": True,
            "structure_reclaimed_bullish": False,
            "bullish_structure_active": False,
            "structure_reclaimed_bearish": False,
        },
    )

    adjusted, penalty, note = _regime_adjustment("Long", 92, snapshot, ["price closed above 50 EMA"] * 4)

    assert adjusted == 37
    assert penalty == -55
    assert note and "Minor bounces are disabled until price reclaims structure" in note


def test_reclaimed_high_quality_long_can_trade_against_bearish_structure():
    snapshot = MarketRegimeSnapshot(
        score=-52,
        label="Strong Bearish",
        regime_type="TRENDING_DOWN",
        confidence_score=84,
        confidence_breakdown={"bearish": 84},
        structure="LH/LL",
        is_transition=False,
        trade_decision="TRADE_ALLOWED",
        bias="Short bias",
        long_bias="Counter-trend longs require strong reversal confirmation",
        short_bias="Prioritize shorts",
        bias_reason="bearish structure active",
        bias_flip_trigger="bearish structure active",
        updated_at=datetime.now(UTC),
        components={
            "bearish_structure_active": True,
            "structure_reclaimed_bullish": True,
            "bullish_structure_active": False,
            "structure_reclaimed_bearish": False,
        },
    )

    adjusted, penalty, note = _regime_adjustment(
        "Long",
        88,
        snapshot,
        ["price closed above 50 EMA", "bullish market structure break", "bullish momentum confirmation"],
    )

    assert adjusted == 73
    assert penalty == -15
    assert note and "Counter-trend long allowed" in note
