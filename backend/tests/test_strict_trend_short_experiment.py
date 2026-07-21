from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pandas as pd

from app.config import get_settings
from app.models.schemas import LiquiditySweep, MarketRegimeSnapshot, RiskSettings, TradeIdea, Zone
from app.services.strategy_experiments import strict_trend_short_shadow_report
from app.services.trade_history import save_trade_ideas
from app.strategy.strict_trend_short import evaluate_strict_trend_short
from app.strategy.trade_ideas import build_trade_ideas
from app.utils import database


START = datetime(2026, 7, 1, 0, 0, tzinfo=UTC)
SUPPORT = Zone(type="support", lower=90.0, upper=92.0, strength=0.8, touches=4)
RESISTANCE = Zone(type="resistance", lower=108.0, upper=110.0, strength=0.8, touches=4)


def candles(*, high: float = 101.0, low: float = 98.0, close: float = 99.0, volume: float = 1_000.0) -> pd.DataFrame:
    rows = []
    for index in range(60):
        level = 103.0 - index * 0.05
        rows.append(
            {
                "timestamp": START + timedelta(hours=4 * index),
                "open": level + 0.2,
                "high": level + 1.0,
                "low": level - 1.0,
                "close": level,
                "volume": 1_000.0,
            }
        )
    rows[-1].update({"open": close + 0.4, "high": high, "low": low, "close": close, "volume": volume})
    return pd.DataFrame(rows)


def evaluate(df: pd.DataFrame, *, sweeps: list[LiquiditySweep] | None = None, htf_bias: str = "HTF_BEARISH", position: float = 0.8):
    as_of = df.iloc[-1]["timestamp"].to_pydatetime() + timedelta(hours=4, minutes=1)
    return evaluate_strict_trend_short(
        regime="TRENDING_DOWN",
        direction="Short",
        df=df,
        timeframe="4h",
        support=SUPPORT,
        resistance=RESISTANCE,
        sweeps=sweeps or [],
        htf_bias=htf_bias,
        normalized_position=position,
        as_of=as_of,
    )


def test_htf_bearish_plus_momentum_without_trigger_fails():
    result = evaluate(candles())
    assert result.eligible is False
    assert result.trigger_type is None
    assert result.confirmation_type == "htf_bearish_alignment"


def test_range_position_alone_fails():
    result = evaluate(candles(), htf_bias="HTF_NEUTRAL", position=0.95)
    assert result.eligible is False
    assert result.trigger_type is None


def test_resistance_rejection_plus_confirmation_passes():
    result = evaluate(candles(high=108.5, low=106.5, close=107.5))
    assert result.eligible is True
    assert result.trigger_type == "resistance_rejection"
    assert result.trigger_candle_completed is True


def test_failed_reclaim_plus_confirmation_passes():
    result = evaluate(candles(high=90.5, low=88.5, close=89.5))
    assert result.eligible is True
    assert result.trigger_type == "failed_reclaim"


def test_completed_bearish_retest_plus_confirmation_passes():
    result = evaluate(candles(high=91.2, low=90.5, close=91.0))
    assert result.eligible is True
    assert result.trigger_type == "completed_bearish_retest"


def test_bearish_liquidity_sweep_plus_confirmation_passes():
    df = candles()
    sweep_time = df.iloc[-2]["timestamp"].to_pydatetime()
    sweep = LiquiditySweep(
        direction="bearish",
        swept_level=110.0,
        candle_time=sweep_time,
        reclaim_price=107.0,
        strength=0.9,
        confirmation_status="confirmed",
    )
    result = evaluate(df, sweeps=[sweep])
    assert result.eligible is True
    assert result.trigger_type == "bearish_liquidity_sweep"
    assert result.trigger_candle_time == sweep_time + timedelta(hours=4)


def test_stale_bearish_liquidity_sweep_does_not_qualify_a_new_opportunity():
    df = candles()
    sweep = LiquiditySweep(
        direction="bearish",
        swept_level=110.0,
        candle_time=df.iloc[-3]["timestamp"].to_pydatetime(),
        reclaim_price=107.0,
        strength=0.9,
        confirmation_status="confirmed",
    )
    result = evaluate(df, sweeps=[sweep])
    assert result.eligible is False
    assert result.trigger_type is None


def test_forming_trigger_candle_is_not_used():
    df = candles(high=108.5, low=106.5, close=107.5)
    result = evaluate_strict_trend_short(
        regime="TRENDING_DOWN",
        direction="Short",
        df=df,
        timeframe="4h",
        support=SUPPORT,
        resistance=RESISTANCE,
        sweeps=[],
        htf_bias="HTF_BEARISH",
        as_of=df.iloc[-1]["timestamp"].to_pydatetime() + timedelta(hours=3),
    )
    assert result.eligible is False
    assert result.trigger_type is None


def test_confirmed_structural_break_plus_confirmation_passes():
    result = evaluate(candles(high=88.0, low=86.5, close=87.5))
    assert result.eligible is True
    assert result.trigger_type == "confirmed_structure_break"


def sample_idea(*, candle_time: datetime, strict: bool, entry_status: str = "READY") -> TradeIdea:
    return TradeIdea(
        symbol="BTCUSDT",
        timeframe="4h",
        exchange="hyperliquid",
        direction="Short",
        market_regime="TRENDING_DOWN",
        entry_zone=(100.0, 101.0),
        stop_loss=105.0,
        take_profit_1=94.0,
        take_profit_2=88.0,
        risk_reward_ratio=2.5,
        reason="Production setup remains unchanged.",
        confidence_score=82,
        setup_score=82,
        invalid_condition="Invalid above stop.",
        entry_status=entry_status,
        signal_candle_time=candle_time,
        production_rule_accepted=True,
        strict_trend_short_eligible=strict,
        strict_trigger_type="resistance_rejection" if strict else None,
        strict_confirmation_type="htf_bearish_alignment" if strict else "bearish_momentum",
        strict_trigger_candle_time=candle_time if strict else None,
        strict_trigger_candle_completed=True,
    )


def configure_db(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'swiftchart.db'}")
    monkeypatch.setenv("ALERT_DEDUPE_STATE_PATH", str(tmp_path / "dedupe.json"))
    get_settings.cache_clear()
    database._INITIALIZED = False


def test_shadow_metadata_does_not_change_v2_shadow_or_telegram_eligibility():
    from bot.alerts import is_limit_order_alertable
    from app.strategy.decision_engine import evaluate_strategy_decision

    strict_false = sample_idea(candle_time=START, strict=False)
    strict_true = sample_idea(candle_time=START, strict=True)

    assert strict_false.entry_status == strict_true.entry_status == "READY"
    assert evaluate_strategy_decision(strict_false) == "SHADOW"
    assert evaluate_strategy_decision(strict_true) == "SHADOW"
    assert is_limit_order_alertable(strict_false) is False
    assert is_limit_order_alertable(strict_true) is False


def test_existing_production_path_still_accepts_position_plus_htf_momentum(monkeypatch):
    from app.strategy import trade_ideas

    df = candles(high=102.0, low=100.0, close=101.0)
    snapshot = MarketRegimeSnapshot(
        score=-65,
        label="Strong Bearish",
        regime_type="TRENDING_DOWN",
        confidence_score=84,
        confidence_breakdown={"bearish": 84},
        structure="LH/LL",
        trade_decision="TRADE_ALLOWED",
        bias="Short bias",
        long_bias="Counter-trend longs require reversal confirmation",
        short_bias="Prioritize shorts",
        updated_at=datetime.now(UTC),
        components={
            "bearish_ema_momentum": True,
            "structural_support_break": False,
            "breakdown_confirmed": False,
        },
    )

    # Keep this regression focused on admission and shadow metadata, not the
    # separate exhaustion-quality layer.
    monkeypatch.setattr(
        trade_ideas,
        "_signal_quality_control",
        lambda **kwargs: {
            "score": kwargs["score"],
            "maturity": "Early",
            "risk": "Low",
            "status": "READY",
            "reasons": [],
            "adjustment": 0.0,
        },
    )

    ideas, _, _ = build_trade_ideas(
        "TESTUSDT",
        "4h",
        "hyperliquid",
        df,
        SUPPORT,
        RESISTANCE,
        [],
        RiskSettings(min_rr=0.5),
        "TRENDING_DOWN",
        "HTF_BEARISH",
        snapshot,
    )

    assert len(ideas) == 1
    assert ideas[0].production_rule_accepted is True
    assert ideas[0].strict_trend_short_eligible is False
    assert ideas[0].strict_trigger_type is None
    assert ideas[0].strict_confirmation_type == "htf_bearish_alignment"
    assert ideas[0].strategy_version == "v1"
    assert ideas[0].edge_status == "EXPERIMENTAL"
    assert ideas[0].strategy_decision == "SHADOW"


def test_shadow_metadata_does_not_change_auto_execution(monkeypatch, tmp_path):
    configure_db(monkeypatch, tmp_path)
    monkeypatch.setenv("EXECUTION_AUTOTRADE_ENABLED", "true")
    monkeypatch.setenv("EXECUTION_SIGNAL_WEBHOOK_URL", "https://executor.invalid/signal")
    get_settings.cache_clear()
    from app.services import execution_signals

    calls = []

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"accepted": True, "reason": "test"}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, *args, **kwargs):
            calls.append((args, kwargs))
            return Response()

    execution_signals._sent_signal_ids.clear()
    monkeypatch.setattr(execution_signals.httpx, "AsyncClient", lambda **kwargs: FakeClient())
    asyncio.run(execution_signals.dispatch_trade_ideas_to_execution([sample_idea(candle_time=START, strict=False)]))
    assert len(calls) == 1


def test_shadow_report_compares_canonical_completed_opportunities(monkeypatch, tmp_path):
    configure_db(monkeypatch, tmp_path)
    opportunities = [
        sample_idea(candle_time=START, strict=True),
        sample_idea(candle_time=START + timedelta(hours=4), strict=False),
        sample_idea(candle_time=START + timedelta(hours=8), strict=True),
        sample_idea(candle_time=START + timedelta(hours=12), strict=True, entry_status="WAIT_FOR_RETEST"),
    ]
    save_trade_ideas(opportunities)
    with database.get_connection() as connection:
        executable = connection.execute(
            "SELECT id FROM trade_ideas WHERE executable_at IS NOT NULL ORDER BY signal_candle_time"
        ).fetchall()
        outcomes = [("WIN", 2.0), ("LOSS", -1.0), ("LOSS", -1.0)]
        for row, (result, pnl) in zip(executable, outcomes, strict=True):
            connection.execute(
                "UPDATE trade_ideas SET status=?, result=?, pnl_r_multiple=?, closed_at=? WHERE id=?",
                ("TP2_HIT" if result == "WIN" else "SL_HIT", result, pnl, (START + timedelta(days=2)).isoformat(), row["id"]),
            )

    report = strict_trend_short_shadow_report()
    current = report["current_production_rule"]
    strict = report["experimental_strict_rule"]

    assert current["unique_opportunities"] == 3
    assert current["completed_outcomes"] == 3
    assert current["wins"] == 1
    assert current["losses"] == 2
    assert current["expectancy_r"] == 0.0
    assert strict["unique_opportunities"] == 2
    assert strict["wins"] == 1
    assert strict["losses"] == 1
    assert strict["expectancy_r"] == 0.5
    assert strict["total_r"] == 1.0
    assert strict["retained_percentage"] == 66.67
