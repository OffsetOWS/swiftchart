from app.config import get_settings
from app.models.schemas import TradeIdea
from app.services.genlayer_ai import (
    list_validation_history,
    mock_validate_signal,
    trade_idea_to_genlayer_signal,
    validate_and_store_signal,
)
from app.utils import database


def sample_idea(**overrides) -> TradeIdea:
    data = {
        "symbol": "BTCUSDT",
        "timeframe": "4h",
        "exchange": "hyperliquid",
        "source": "hyperliquid",
        "direction": "Long",
        "entry_zone": (100.0, 102.0),
        "stop_loss": 96.0,
        "take_profit_1": 110.0,
        "take_profit_2": 118.0,
        "risk_reward_ratio": 2.5,
        "reason": "Clean SwiftChart setup.",
        "confidence_score": 82,
        "setup_score": 82,
        "higher_timeframe_bias": "HTF_BULLISH",
        "regime_label": "Trending up",
        "invalid_condition": "Break below support.",
        "rank_score": 91,
    }
    data.update(overrides)
    return TradeIdea(**data)


def test_trade_idea_to_genlayer_signal_keeps_swiftchart_signal_structured():
    payload = trade_idea_to_genlayer_signal(sample_idea())

    assert payload.symbol == "BTCUSDT"
    assert payload.side == "BUY"
    assert payload.entry == 101.0
    assert payload.take_profits == [110.0, 118.0]
    assert payload.risk_to_reward == 2.5
    assert payload.market_regime == "Trending up"
    assert payload.htf_bias == "HTF_BULLISH"
    assert payload.invalidation_condition == "Break below support."


def test_mock_validator_can_approve_and_paper_execute_good_signal():
    result = mock_validate_signal(sample_idea())

    assert result.decision == "APPROVE"
    assert result.paper_execution_status == "PAPER_EXECUTED"
    assert result.recommended_position_size > 0
    assert len(result.validator_votes) == 3


def test_mock_validator_waits_for_retest_without_paper_execution():
    result = mock_validate_signal(sample_idea(entry_status="WAIT_FOR_RETEST"))

    assert result.decision == "WAIT"
    assert result.paper_execution_status == "NOT_EXECUTED"
    assert "Wait for retest before entry." in result.warning_flags


def test_validate_and_store_signal_persists_history(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'swiftchart.db'}")
    get_settings.cache_clear()
    database._INITIALIZED = False

    try:
        stored = validate_and_store_signal(sample_idea())
        history = list_validation_history()

        assert stored.id is not None
        assert history[0].id == stored.id
        assert history[0].signal.symbol == "BTCUSDT"
        assert history[0].decision == "APPROVE"
    finally:
        get_settings.cache_clear()
        database._INITIALIZED = False
