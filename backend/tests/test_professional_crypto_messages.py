import re

from app.models.schemas import TradeIdea
from bot.formatter import format_outcome_event, format_trade_alert


def make_idea(**updates):
    values = dict(
        symbol="HYPE", timeframe="1h", exchange="hyperliquid", direction="Long",
        setup_family="bounce_long", strategy_version="v1", entry_zone=(60.87, 60.87),
        stop_loss=59.97, take_profit_1=65.32, take_profit_2=65.32,
        risk_reward_ratio=4.94, reason=("Price swept prior support at 59.71 and printed a low of 55.78, "
        "then reclaimed 59.71 with a close at 60.01. Since then it held a higher low at 60.22 "
        "and closed above the 61.52 bounce high, suggesting buyers have taken short-term control."),
        confidence_score=85, setup_score=85, invalid_condition="", price_decimals=2,
        regime_type="TRENDING_DOWN", higher_timeframe_bias="HTF_BEARISH",
    )
    values.update(updates)
    return TradeIdea(**values)


def test_bounce_and_trend_message_snapshots_and_field_numbers():
    bounce = make_idea()
    message = format_trade_alert(bounce)
    assert message == (
        "🔵 BOUNCE LONG · HYPE · 1H\n\n"
        "Counter-trend trade: a quick move back toward resistance. Price swept prior support at 59.71 and printed a low of 55.78, "
        "then reclaimed 59.71 with a close at 60.01. Since then it held a higher low at 60.22 and closed above the 61.52 bounce high, "
        "suggesting buyers have taken short-term control. The market is in a downtrend. The higher-timeframe bias is bearish.\n\n"
        "Plan\nEntry: 60.87 (limit retest filled)\nStop: 59.97\nTarget: 65.32 (TP1 and TP2, split 50/50)\nRisk/reward: 1 : 4.9\n\n"
        "The stop is hit if a 1H candle trades below 59.97. As a counter-trend trade, the aim is the target, not a trend reversal. "
        "Expires in the 48-hour window."
    )
    assert "85" not in message
    numeric_literals = {"59.71", "55.78", "60.01", "60.22", "61.52", "60.87", "59.97", "65.32", "4.9", "1", "48", "50", "2"}
    assert set(re.findall(r"\d+(?:\.\d+)?", message)) <= numeric_literals | {"4"}

    short = make_idea(setup_family="trend_continuation", direction="Short",
                      entry_zone=(3120, 3120), stop_loss=3185, take_profit_1=3010,
                      take_profit_2=2940, price_decimals=0, risk_reward_ratio=2.8,
                      reason="Price is in a downtrend. The latest rally tested prior resistance between 3150 and 3180 and was rejected.",
                      regime_type="TRENDING_DOWN", higher_timeframe_bias="HTF_BEARISH")
    short_message = format_trade_alert(short)
    assert short_message == (
        "🔴 TREND SHORT · HYPE · 1H\n\nWith-trend trade: the broader structure keeps sellers in control. "
        "Price is in a downtrend. The latest rally tested prior resistance between 3,150 and 3,180 and was rejected. "
        "The higher-timeframe bias is bearish.\n\n"
        "Plan\nEntry: 3,120\nStop: 3,185\nTP1: 3,010 (50%) · TP2: 2,940 (remaining 50%)\n"
        "Risk/reward: 1 : 1.7 (TP1) · 1 : 2.8 (TP2)\n\n"
        "The stop is hit if a 1H candle trades above 3,185. Expires in the 48-hour window."
    )

    long = make_idea(setup_family="trend_continuation", direction="Long",
                     reason="Price is in an uptrend. The latest pullback tested prior support between 60 and 61 and held.",
                     regime_type="TRENDING_UP", higher_timeframe_bias="HTF_BULLISH")
    assert format_trade_alert(long) == (
        "🟢 TREND LONG · HYPE · 1H\n\nWith-trend trade: the broader structure supports continuation higher. "
        "Price is in an uptrend. The latest pullback tested prior support between 60.00 and 61.00 and held. "
        "The higher-timeframe bias is bullish.\n\n"
        "Plan\nEntry: 60.87\nStop: 59.97\nTP1: 65.32 (50%) · TP2: 65.32 (remaining 50%)\n"
        "Risk/reward: 1 : 4.9 (TP1) · 1 : 4.9 (TP2)\n\n"
        "The stop is hit if a 1H candle trades below 59.97. Expires in the 48-hour window."
    )


def test_missing_evidence_drops_cleanly_and_pending_waits_for_fill():
    from app.strategy.decision_engine import evaluate_strategy_decision

    idea = make_idea(reason="", regime_type=None, higher_timeframe_bias="HTF_NEUTRAL",
                     entry_status="WAIT_FOR_RETEST")
    assert evaluate_strategy_decision(idea) == "WAIT_FOR_RETEST"
    message = format_trade_alert(idea)
    assert message == (
        "🔵 BOUNCE LONG · HYPE · 1H\n\nCounter-trend trade: a quick move back toward resistance. "
        "The higher-timeframe bias is neutral.\n\nPending setup\nLimit entry: 60.87\nStop: 59.97\n"
        "Valid for 6 candles. The alert is sent only after the retest fills."
    )
    # The canonical dispatcher selects only READY + TRADE rows, so this pending
    # six-candle limit never generates a Telegram alert before it fills.
    assert idea.entry_status != "READY" and idea.strategy_decision != "TRADE"


def test_tp_outbox_alert_has_tag_target_and_realized_r():
    message = format_outcome_event({
        "setup_family": "bounce_long", "event_kind": "TP1", "symbol": "HYPE",
        "direction": "LONG", "price": 65.32, "quantity": .5,
        "entry_zone_low": 60.87, "entry_zone_high": 60.87, "stop_loss": 59.97,
        "price_decimals": 2,
    })
    assert message == "🔵 Bounce long HYPE hit TP1 ✅ +2.5R\nPrice: 65.32"
