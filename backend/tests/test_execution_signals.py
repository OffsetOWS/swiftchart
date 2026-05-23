from app.models.schemas import TradeIdea
import pytest

from app.services import execution_signals
from app.services.execution_signals import dispatch_trade_ideas_to_execution, execution_signal_id, trade_idea_to_execution_signal


def sample_idea(direction: str = "Long", entry_status: str = "READY") -> TradeIdea:
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
        entry_status=entry_status,
    )


def test_trade_idea_to_execution_signal_maps_long_to_buy():
    payload = trade_idea_to_execution_signal(sample_idea("Long"))

    assert payload["pair"] == "BTCUSDT"
    assert payload["side"] == "BUY"
    assert payload["entry"] == 101.0
    assert payload["confidence"] == 82
    assert payload["timeframe"] == "4h"
    assert payload["exchange"] == "hyperliquid"
    assert payload["entry_status"] == "READY"
    assert payload["move_maturity"] == "Early"
    assert payload["exhaustion_risk"] == "Low"
    assert payload["signal_id"].startswith("swiftchart-")


def test_trade_idea_to_execution_signal_maps_short_to_sell():
    payload = trade_idea_to_execution_signal(sample_idea("Short"))

    assert payload["side"] == "SELL"


def test_execution_signal_id_is_stable_for_same_trade_shape():
    idea = sample_idea()

    assert execution_signal_id(idea) == execution_signal_id(idea.model_copy())


@pytest.mark.anyio
async def test_execution_dispatch_ignores_non_ready_watchlist_candidate(monkeypatch, tmp_path):
    posts = []

    class Settings:
        execution_autotrade_enabled = True
        execution_signal_webhook_url = "https://example.test/webhook"
        execution_webhook_secret = ""

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"accepted": True}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, *args, **kwargs):
            posts.append((args, kwargs))
            return FakeResponse()

    monkeypatch.setenv("BOT_STATE_PATH", str(tmp_path / "state.json"))
    monkeypatch.setattr(execution_signals, "get_settings", lambda: Settings())
    monkeypatch.setattr(execution_signals.httpx, "AsyncClient", FakeClient)

    await dispatch_trade_ideas_to_execution([sample_idea(entry_status="WAIT_FOR_RETEST")])

    assert posts == []
