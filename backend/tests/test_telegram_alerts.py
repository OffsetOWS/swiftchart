from __future__ import annotations

from datetime import UTC, datetime

import pytest

pytest.importorskip("telegram")

from app.models.schemas import TradeIdea
from bot import alerts


def idea(score: float = 82, entry_status: str = "READY") -> TradeIdea:
    return TradeIdea(
        symbol="BTCUSDT",
        timeframe="4h",
        exchange="hyperliquid",
        direction="Long",
        entry_zone=(100.0, 101.0),
        stop_loss=96.0,
        take_profit_1=110.0,
        take_profit_2=118.0,
        risk_reward_ratio=2.4,
        reason="Clean setup.",
        confidence_score=score,
        setup_score=score,
        invalid_condition="Invalid below support.",
        entry_status=entry_status,
        signal_candle_time=datetime(2026, 5, 23, 0, 0, tzinfo=UTC),
    )


class FakeBot:
    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.messages = []

    async def send_message(self, chat_id: int, text: str) -> None:
        if self.fail:
            raise RuntimeError("telegram unavailable")
        self.messages.append((chat_id, text))


@pytest.mark.anyio
async def test_alert_scan_filters_by_score_and_entry_status(monkeypatch, tmp_path):
    monkeypatch.setenv("BOT_STATE_PATH", str(tmp_path / "state.json"))
    monkeypatch.setenv("ALERT_MIN_SCORE", "75")
    monkeypatch.setattr(alerts, "get_subscribers", lambda: {123})

    async def fake_scan_top_ideas(timeframe: str, exchange: str):
        return [idea(70), idea(82, "WAIT_FOR_RETEST")], "hyperliquid"

    monkeypatch.setattr(alerts, "scan_top_ideas", fake_scan_top_ideas)

    result = await alerts.run_alert_scan(FakeBot())

    assert result["ideas"] == 2
    assert result["eligible"] == 0
    assert result["sent"] == 0
    assert "rejection_reasons" not in result


@pytest.mark.anyio
async def test_alert_scan_marks_alert_processed_after_send_attempt(monkeypatch, tmp_path):
    monkeypatch.setenv("BOT_STATE_PATH", str(tmp_path / "state.json"))
    monkeypatch.setenv("ALERT_MIN_SCORE", "75")
    monkeypatch.setattr(alerts, "get_subscribers", lambda: {123})

    async def fake_scan_top_ideas(timeframe: str, exchange: str):
        return [idea()], "hyperliquid"

    monkeypatch.setattr(alerts, "scan_top_ideas", fake_scan_top_ideas)

    result = await alerts.run_alert_scan(FakeBot(fail=True))
    retry = await alerts.run_alert_scan(FakeBot())

    assert result["eligible"] == 1
    assert result["sent"] == 0
    assert "failed" not in result
    assert retry["sent"] == 0
