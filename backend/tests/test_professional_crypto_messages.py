import re

import pytest

from app.models.schemas import TradeIdea
from bot.formatter import format_outcome_event, format_trade_alert


def make_idea(**updates):
    values = dict(
        symbol="HYPE", timeframe="1h", exchange="hyperliquid", direction="Long",
        setup_family="bounce_long", strategy_version="v1", entry_zone=(60.87, 60.87),
        stop_loss=59.97, take_profit_1=65.32, take_profit_2=65.32,
        risk_reward_ratio=4.94, reason=("Price swept prior support at 59.71 and printed a low of 55.78, "
        "then reclaimed 59.71 with a close at 60.01. Since then it held a higher low at 60.22 "
        "and closed above the 61.52 bounce high, suggesting buyers have taken short-term control. "
        "Stop anchor swing level: 60.22. Target basis: resistance."),
        confidence_score=85, setup_score=85, invalid_condition="", price_decimals=2,
        regime_type="TRENDING_DOWN", higher_timeframe_bias="HTF_BEARISH",
    )
    values.update(updates)
    return TradeIdea(**values)


@pytest.mark.parametrize(
    ("btc_regime", "direction", "effect"),
    [
        ("TRENDING_DOWN", "Short", "a tailwind for this short"),
        ("TRENDING_DOWN", "Long", "a headwind for this long"),
        ("TRENDING_UP", "Long", "a tailwind for this long"),
        ("TRENDING_UP", "Short", "a headwind for this short"),
        ("RANGE_BOUND", "Long", "neutral for this long"),
        ("RANGE_BOUND", "Short", "neutral for this short"),
    ],
)
def test_btc_context_is_direction_aware_for_all_six_combinations(btc_regime, direction, effect):
    idea = make_idea(direction=direction, setup_family="trend_continuation",
                     reason="The latest rally tested prior resistance between 60 and 61, then sellers rejected it.")
    message = format_trade_alert(idea, {"btc_4h_regime": btc_regime})
    assert effect in message
    assert message.rstrip().endswith(".")


def test_bounce_template_order_numbers_and_trade_management():
    message = format_trade_alert(make_idea(reason=(
        "Price swept prior support at 59.71 and printed a low of 55.78, then reclaimed "
        "59.71 with a close at 60.01. After the reclaim, price held a higher low at 60.22 and closed "
        "above the 61.52 bounce high, suggesting buyers have taken short-term control. "
        "BTC is in a downtrend on the 4H, adding a broad-market headwind. "
        "Stop anchor swing level: 60.22. Target basis: resistance.")))
    assert message == (
        "🔵 BOUNCE LONG · HYPE · 1H\n\n"
        "Signal\nEntry: 60.87 (limit retest filled)\nStop: 59.97 (below swing low at 60.22)\nTarget: 65.32 (resistance)\n"
        "Risk/reward: 1 : 4.9\n\nValid for 48 hours."
        "\n\nWhy this setup\n"
        "Counter-trend long against a bearish higher-timeframe trend. "
        "Price swept prior support at 59.71 and printed a low of 55.78, then reclaimed "
        "59.71 with a close at 60.01. After the reclaim, price held a higher low at 60.22 and closed above the 61.52 bounce high, "
        "suggesting buyers have taken short-term control. BTC is trending lower on the 4H, a headwind for this long."
    )
    assert message.index("Entry:") < message.index("Why this setup")
    assert "85" not in message
    numbers = set(re.findall(r"\d+(?:,\d{3})*(?:\.\d+)?", message))
    assert numbers <= {"59.71", "55.78", "60.01", "60.22", "61.52", "60.87", "59.97", "65.32", "4.9", "1", "48", "4"}


def test_trend_short_rejects_internal_reason_and_uses_trade_zone_and_stop_reason():
    short = make_idea(
        symbol="FETUSDT", setup_family="trend_continuation", direction="Short",
        entry_zone=(0.10959, 0.10991), stop_loss=0.111437,
        take_profit_1=0.107043, take_profit_2=0.10475, price_decimals=5,
        reason=("Price is in a downtrend. The latest rally tested prior resistance between 0.10534 and 0.10608 and was rejected. "
                "Signal is with the active market regime. Entry Status: READY. score=91. "
                "logger.warning('raw reason field'). Stop distance: 0.8 ATR above entry. "
                "BTC is in a downtrend on the 4H, adding a broad-market headwind."),
        regime_type="TRENDING_DOWN", higher_timeframe_bias="HTF_BEARISH",
    )
    message = format_trade_alert(short)
    assert "0.10534" not in message and "0.10608" not in message
    assert "The trend structure is bearish, with lower highs and lower lows." in message
    assert "Stop: 0.11144 (0.8 ATR above entry)" in message
    assert "a tailwind for this short" in message
    assert "Signal is with the active market regime" not in message
    assert "Entry Status" not in message and "score=91" not in message and "logger.warning" not in message
    assert "Price is in a downtrend." not in message
    assert "Valid for 48 hours." in message


def test_short_fallback_reason_does_not_mislabel_scoring_support_as_rejected_resistance():
    from app.models.schemas import Zone
    from app.strategy.trade_ideas import _reason

    scoring_support = Zone(type="support", lower=0.10534, upper=0.10608, strength=.8, touches=4)
    reason = _reason("TRENDING_DOWN", "Short", None, scoring_support, True, False,
                     entry_low=0.10959, entry_high=0.10991, stop=0.111437,
                     trigger_sentence="Price is trading below the 21 EMA and below the 55 EMA.",
                     stop_atr_multiple=0.8)
    assert "prior resistance" not in reason
    assert "Price is trading below the 21 EMA" in reason
    assert "Volume supported the bearish move" in reason


def test_real_rejected_zone_drives_short_trigger_and_stop_explanation():
    short = make_idea(
        setup_family="trend_continuation", direction="Short", entry_zone=(3120, 3135),
        stop_loss=3185, take_profit_1=3010, take_profit_2=2940, price_decimals=0,
        reason="The latest rally tested prior resistance between 3150 and 3180, then sellers rejected it. Stop anchor swing level: 3180.",
        regime_type="TRENDING_DOWN", higher_timeframe_bias="HTF_BEARISH",
    )
    message = format_trade_alert(short)
    assert "The latest rally tested prior resistance between 3,150 and 3,180" in message
    assert "Stop: 3,185 (above swing level 3,180)" in message
    assert "TP1: 3,010 (50%) · TP2: 2,940 (remaining 50%)" in message


@pytest.mark.parametrize(
    ("direction", "reason", "expected"),
    [
        ("Short", "Price rallied into resistance at 0.111 and was rejected.", "0.111"),
        ("Short", "Price retested broken support at 0.108 and failed to reclaim it.", "0.108"),
        ("Short", "Price swept resistance at 0.111, then sellers rejected the move.", "swept resistance at 0.111"),
        ("Short", "Price broke below support at 0.108.", "0.108"),
        ("Long", "Price tested support at 0.108, and buyers defended support at 0.108.", "buyers defended support at 0.108"),
        ("Long", "Price retested broken resistance at 0.111 and held above resistance at 0.111.", "0.111"),
        ("Long", "Price swept support at 0.108 and reclaimed support at 0.109.", "swept support at 0.108"),
        ("Long", "Price reclaimed resistance at 0.111 and held above resistance at 0.111.", "0.111"),
        ("Short", "Price is trading below the 21 EMA and below the 55 EMA.", "trading below the 21 EMA"),
    ],
)
def test_every_trend_message_names_the_fired_trigger_or_structure_fallback(direction, reason, expected):
    idea = make_idea(
        setup_family="trend_continuation", direction=direction,
        entry_zone=(0.109, 0.110) if direction == "Short" else (0.110, 0.111),
        stop_loss=0.112 if direction == "Short" else 0.108,
        take_profit_1=0.107 if direction == "Short" else 0.113,
        take_profit_2=0.105 if direction == "Short" else 0.115,
        reason=f"{reason} Stop distance: 1.0 ATR {'above' if direction == 'Short' else 'below'} entry.",
        regime_type="TRENDING_DOWN" if direction == "Short" else "TRENDING_UP",
        higher_timeframe_bias="HTF_BEARISH" if direction == "Short" else "HTF_BULLISH",
        price_decimals=3,
    )
    message = format_trade_alert(idea)
    assert expected in message
    assert message.index("Entry:") < message.index("Why this setup")
    assert expected in message.split("Why this setup\n", 1)[1]
    assert "short entry is placed near current price" not in message


def test_trend_long_single_target_and_chop_label_are_honest():
    idea = make_idea(setup_family="trend_continuation", direction="Long", regime_type="CHOP",
                     market_regime="CHOP", higher_timeframe_bias="HTF_BULLISH",
                     reason="The latest pullback tested prior support between 60 and 61, then buyers held it. "
                     "Stop anchor swing level: 59.97. Target basis: resistance.")
    message = format_trade_alert(idea)
    assert "range" in message.lower()
    assert "HYPE" in message and "1H" in message
    assert "The market is choppy." not in message
    assert "Target: 65.32" in message
    assert "TP1:" not in message and "TP2:" not in message


def test_missing_evidence_drops_cleanly_and_pending_waits_for_fill():
    from app.strategy.decision_engine import evaluate_strategy_decision

    idea = make_idea(reason="Stop anchor swing level: 60.22.", regime_type=None, higher_timeframe_bias="HTF_NEUTRAL",
                     entry_status="WAIT_FOR_RETEST")
    assert evaluate_strategy_decision(idea) == "WAIT_FOR_RETEST"
    message = format_trade_alert(idea)
    assert "Counter-trend long" in message
    assert "HYPE is in a" not in message
    assert "Limit entry: 60.87\nStop: 59.97 (below swing low at 60.22)" in message
    assert "Valid for 6 candles. The alert is sent only after the retest fills." in message
    assert "Valid for 48 hours" not in message
    assert idea.entry_status != "READY" and idea.strategy_decision != "TRADE"


def test_signal_plan_is_first_and_phrasing_is_varied_but_repeatable():
    reason = (
        "Price rallied into resistance at 0.113268 and was rejected. "
        "Downside momentum remains present. Stop anchor swing level: 0.115312."
    )
    base = dict(setup_family="trend_continuation", direction="Short", reason=reason,
                price_decimals=6, regime_type="TRENDING_DOWN", higher_timeframe_bias="HTF_BEARISH",
                entry_zone=(0.11178, 0.113352), stop_loss=0.117583,
                take_profit_1=0.104791, take_profit_2=0.098502)
    arb = make_idea(symbol="ARB", **base)
    first = format_trade_alert(arb, {"btc_4h_regime": "RANGE_BOUND"})
    assert first == format_trade_alert(arb, {"btc_4h_regime": "RANGE_BOUND"})
    assert first.startswith("🔴 TREND SHORT")
    assert first.index("Signal") < first.index("Why this setup")
    assert first.index("Entry:") < first.index("Why this setup")
    assert first.index("Why this setup") < first.index("resistance")
    assert "conditions neutral for this short" in first

    init = make_idea(symbol="INIT", **base)
    second = format_trade_alert(init, {"btc_4h_regime": "RANGE_BOUND"})
    first_context = first.split("Why this setup\n", 1)[1].split(". ", 1)[0]
    second_context = second.split("Why this setup\n", 1)[1].split(". ", 1)[0]
    assert first_context != second_context


def test_tp_outbox_alert_shows_trade_r_realized_r_and_actual_default_stop_rule(monkeypatch):
    monkeypatch.setenv("TRADE_HISTORY_MOVE_STOP_TO_ENTRY_AFTER_TP1", "false")
    from app.config import get_settings
    get_settings.cache_clear()
    message = format_outcome_event({
        "setup_family": "trend_continuation", "event_kind": "TP1", "symbol": "FETUSDT",
        "direction": "SHORT", "price": 0.107043, "quantity": .5,
        "entry_zone_low": 0.10959, "entry_zone_high": 0.10991, "stop_loss": 0.111437,
        "price_decimals": 5,
    })
    assert message == (
        "🔴 Trend short FET/USDT hit TP1 ✅ +1.6R on half the position (+0.8R banked). "
        "The stop stays at its original level, 0.11144.\nPrice: 0.10704"
    )
    assert "91" not in message
