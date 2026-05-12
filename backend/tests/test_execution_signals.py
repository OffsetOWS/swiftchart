from app.models.schemas import TradeIdea
from app.services.execution_signals import execution_signal_id, trade_idea_to_execution_signal


def sample_idea(direction: str = "Long") -> TradeIdea:
    return TradeIdea(
        symbol="BTCUSDT",
        timeframe="4h",
        exchange="hyperliquid",
        direction=direction,
        entry_zone=(100.0, 102.0),
        stop_loss=96.0,
        take_profit_1=110.0,
        take_profit_2=118.0,
        risk_reward_ratio=2.5,
        reason="Clean SwiftChart setup.",
        confidence_score=82,
        invalid_condition="Break below support.",
        rank_score=91,
    )


def test_trade_idea_to_execution_signal_maps_long_to_buy():
    payload = trade_idea_to_execution_signal(sample_idea("Long"))

    assert payload["pair"] == "BTCUSDT"
    assert payload["side"] == "BUY"
    assert payload["entry"] == 101.0
    assert payload["confidence"] == 82
    assert payload["timeframe"] == "4h"
    assert payload["exchange"] == "hyperliquid"
    assert payload["signal_id"].startswith("swiftchart-")


def test_trade_idea_to_execution_signal_maps_short_to_sell():
    payload = trade_idea_to_execution_signal(sample_idea("Short"))

    assert payload["side"] == "SELL"


def test_execution_signal_id_is_stable_for_same_trade_shape():
    idea = sample_idea()

    assert execution_signal_id(idea) == execution_signal_id(idea.model_copy())
