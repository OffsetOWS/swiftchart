from datetime import UTC, datetime

import pandas as pd

from app.strategy.bounce_market_filter import blocks_bounce_long


def test_pre_registered_market_filters_use_fixed_thresholds():
    context = {
        "btc_4h_regime": "TRENDING_DOWN",
        "global_regime_score_4h": -45,
        "btc_1d_below_ema50": True,
        "btc_1d_ema50_slope_10d": -0.01,
    }
    assert blocks_bounce_long("F1", context)
    assert blocks_bounce_long("F2", context)
    assert blocks_bounce_long("F3", context)
    assert not blocks_bounce_long("F1", {**context, "btc_4h_regime": "CHOP"})
    assert not blocks_bounce_long("F2", {**context, "global_regime_score_4h": -44.9})
    assert not blocks_bounce_long("F3", {**context, "btc_1d_below_ema50": False})
    assert not blocks_bounce_long("F3", {**context, "btc_1d_ema50_slope_10d": 0})


def test_market_filter_missing_context_fails_closed():
    assert blocks_bounce_long("F1", None)
    assert blocks_bounce_long("F2", {"global_regime_score_4h": None})
    assert blocks_bounce_long("F3", {"btc_1d_below_ema50": True})
    assert blocks_bounce_long("F4", {})


def test_completed_candle_filter_drops_uncompleted_rows():
    from app.strategy.bounce_market_filter import completed_candles

    frame = pd.DataFrame({"timestamp": [1, 2, 3], "close": [10, 11, 12],
                          "complete": [True, False, True]})
    assert completed_candles(frame, "4h").close.tolist() == [10, 12]


def test_market_context_uses_btc_only_for_f1_and_requires_eth_for_f2(monkeypatch):
    from app.strategy import bounce_market_filter as gates
    from app.models.schemas import MarketRegimeSnapshot

    frame = pd.DataFrame({"timestamp": range(100), "close": [100.0] * 100})
    seen = {}

    def regime(_btc, htf):
        seen["htf"] = htf
        return MarketRegimeSnapshot(score=0, label="Ranging", regime_type="RANGE_BOUND",
                                    confidence_score=50, bias="Neutral", long_bias="Neutral",
                                    short_bias="Neutral", updated_at=datetime.now(UTC))

    monkeypatch.setattr(gates, "detect_market_regime", regime)
    monkeypatch.setattr(gates, "regime_score_from_dataframe", lambda data: -30 if data is frame else -50)
    context_without_eth = gates.build_btc_market_context(frame, frame, None)
    assert seen["htf"] == []
    assert context_without_eth["btc_4h_regime"] == "RANGE_BOUND"
    assert context_without_eth["global_regime_score_4h"] is None
    context_with_eth = gates.build_btc_market_context(frame, frame, frame.copy())
    assert context_with_eth["global_regime_score_4h"] == -40
