from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
import sqlite3

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import get_settings
from app.forex.config import SUPPORTED_FOREX_PAIRS, SUPPORTED_FOREX_TIMEFRAMES
from app.forex.lifecycle import next_signal_status
from app.forex.models import TakeTradeRequest
from app.forex.providers import ForexDataProvider
from app.forex.scanner import scan_forex
from app.forex.storage import (
    get_signal,
    list_signals,
    queue_dispatches,
)
from app.forex.telegram import dispatch_pending_forex, format_forex_signal


class FakeProvider(ForexDataProvider):
    name = "fake"

    def __init__(self) -> None:
        self.timeframes: list[str] = []

    async def candles(self, pair, timeframe: str, limit: int = 240) -> pd.DataFrame:
        self.timeframes.append(timeframe)
        start = datetime(2026, 7, 1, tzinfo=UTC)
        spacing = {"15m": 15, "1h": 60, "4h": 240, "1d": 1440}[timeframe]
        rows = []
        for index in range(90):
            base = 1.0 + index * 0.001
            rows.append(
                {
                    "timestamp": start + timedelta(minutes=spacing * index),
                    "open": base,
                    "high": base + 0.0008,
                    "low": base - 0.0004,
                    "close": base + 0.0005,
                    "volume": 1_000,
                }
            )
        return pd.DataFrame(rows)


class FakeBot:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send_message(self, **kwargs) -> None:
        self.messages.append(kwargs)


@pytest.fixture()
def forex_database(monkeypatch, tmp_path: Path):
    import app.utils.database as database

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'forex.db'}")
    monkeypatch.setenv("BOT_STATE_PATH", str(tmp_path / "bot.json"))
    get_settings.cache_clear()
    database._INITIALIZED = False
    yield
    get_settings.cache_clear()
    database._INITIALIZED = False


def test_signal_list_does_not_call_scanner(monkeypatch, forex_database):
    import app.routes.forex as routes

    async def fail_scan():
        raise AssertionError("GET /forex/signals must never call the scanner")

    monkeypatch.setattr(routes, "scan_forex", fail_scan)
    response = asyncio.run(routes.forex_signals(status_filter=None, limit=100))
    assert response.count == 0


@pytest.mark.parametrize(
    ("timeframe", "provider_timeframe"),
    [("15M", "15m"), ("1H", "1h"), ("4H", "4h"), ("1D", "1d")],
)
def test_each_timeframe_scans_only_itself_and_persists_immutable_signal(
    forex_database,
    timeframe,
    provider_timeframe,
):
    provider = FakeProvider()
    first = asyncio.run(scan_forex(provider, timeframe=timeframe))
    assert first.created
    assert set(provider.timeframes) == {provider_timeframe}

    signal = first.created[0]
    original_levels = (
        signal.entry_low,
        signal.entry_high,
        signal.stop_loss,
        signal.take_profit_1,
        signal.take_profit_2,
    )
    assert signal.id
    assert signal.timeframe == timeframe
    assert signal.bias_timeframe == provider_timeframe
    assert signal.setup_timeframe == provider_timeframe
    assert signal.execution_timeframe == provider_timeframe
    assert timeframe in signal.timeframe_alignment

    second = asyncio.run(scan_forex(provider, timeframe=timeframe))
    assert not second.created
    assert second.reused
    persisted = get_signal(signal.id)
    assert persisted is not None
    assert (
        persisted.entry_low,
        persisted.entry_high,
        persisted.stop_loss,
        persisted.take_profit_1,
        persisted.take_profit_2,
    ) == original_levels
    assert persisted.id == signal.id
    assert len(list_signals(("PENDING_ENTRY",), timeframe=timeframe)) == len(SUPPORTED_FOREX_PAIRS)


def test_timeframes_coexist_and_dedupe_only_within_same_timeframe(forex_database):
    provider = FakeProvider()
    created = {}
    for timeframe in SUPPORTED_FOREX_TIMEFRAMES:
        result = asyncio.run(scan_forex(provider, timeframe=timeframe))
        assert result.created
        created[timeframe] = result.created[0]

    assert len({signal.id for signal in created.values()}) == 4
    assert len({signal.dedupe_key for signal in created.values()}) == 4
    assert {signal.timeframe for signal in list_signals(("PENDING_ENTRY",))} == set(
        SUPPORTED_FOREX_TIMEFRAMES
    )

    duplicate = asyncio.run(scan_forex(provider, timeframe="4H"))
    assert not duplicate.created
    assert duplicate.reused


def test_persisted_signal_plan_fields_are_immutable(forex_database):
    from app.utils.database import get_connection

    signal = asyncio.run(scan_forex(FakeProvider(), timeframe="1H")).created[0]
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        with get_connection() as connection:
            connection.execute(
                "UPDATE forex_signals SET entry_price = ? WHERE public_id = ?",
                (signal.entry_price + 1, signal.id),
            )
    assert get_signal(signal.id).entry_price == signal.entry_price


def test_take_trade_uses_persisted_values_and_rejects_terminal(forex_database):
    import app.routes.forex as routes
    from fastapi import HTTPException

    signal = asyncio.run(scan_forex(FakeProvider())).created[0]
    prepared = asyncio.run(
        routes.take_forex_trade(
            signal.id,
            TakeTradeRequest(
                account_balance=10_000,
                risk_percentage=1,
                execution_method="Copy setup",
            ),
            authorization="Bearer valid-test-session-token",
        )
    )
    assert prepared.signal.id == signal.id
    assert prepared.signal.entry_low == signal.entry_low
    assert prepared.signal.stop_loss == signal.stop_loss
    assert prepared.risk_amount == 100
    assert prepared.execution_status == "PREPARED"

    from app.forex.storage import update_signal_market_state

    update_signal_market_state(
        signal.id,
        status="EXPIRED",
        price=signal.entry_price,
        checked_at=signal.expires_at,
        closed_at=signal.expires_at,
    )
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            routes.take_forex_trade(
                signal.id,
                TakeTradeRequest(
                    account_balance=10_000,
                    risk_percentage=1,
                    execution_method="Copy setup",
                ),
                authorization="Bearer valid-test-session-token",
            )
        )
    assert exc.value.status_code == 409


def test_telegram_uses_exact_persisted_values_and_is_idempotent(forex_database):
    from bot.storage import add_subscriber

    signal = asyncio.run(scan_forex(FakeProvider())).created[0]
    add_subscriber(123)

    message = format_forex_signal(signal)
    assert signal.id in message
    assert f"{signal.entry_low:g} - {signal.entry_high:g}" in message
    assert f"{signal.stop_loss:g}" in message
    assert f"{signal.take_profit_1:g}" in message
    assert f"{signal.take_profit_2:g}" in message
    assert f"Timeframe: {signal.timeframe}" in message

    bot = FakeBot()
    # The bot repairs an outbox gap if no subscriber existed at scan time.
    first = asyncio.run(dispatch_pending_forex(bot))
    assert queue_dispatches(signal.id, ["123"]) == 0
    second = asyncio.run(dispatch_pending_forex(bot))
    assert first["attempted"] == len(list_signals(("PENDING_ENTRY",)))
    assert first["delivered"] == first["attempted"]
    assert first["failed"] == 0
    assert second == {"attempted": 0, "delivered": 0, "failed": 0}
    assert len(bot.messages) == first["delivered"]
    assert any(signal.id in item["text"] for item in bot.messages)


def test_signal_lifecycle_transitions_and_expiry(forex_database):
    signal = asyncio.run(scan_forex(FakeProvider())).created[0]
    opened, activated_at, closed_at = next_signal_status(
        signal,
        price=signal.entry_price,
        checked_at=signal.created_at + timedelta(minutes=15),
    )
    assert opened == "OPEN"
    assert activated_at is not None
    assert closed_at is None

    open_signal = signal.model_copy(update={"status": "OPEN", "activated_at": activated_at})
    tp1_price = signal.take_profit_1 if signal.direction == "LONG" else signal.take_profit_1
    tp1, _, _ = next_signal_status(
        open_signal,
        price=tp1_price,
        checked_at=signal.created_at + timedelta(hours=1),
    )
    assert tp1 == "TP1_HIT"

    expired, _, expired_at = next_signal_status(
        signal,
        price=signal.entry_high + 10,
        checked_at=signal.expires_at,
    )
    assert expired == "EXPIRED"
    assert expired_at == signal.expires_at


def test_scanner_endpoint_is_not_available_to_normal_page_requests(monkeypatch, forex_database):
    from app.routes.forex import router

    monkeypatch.setenv("INTERNAL_API_SECRET", "server-only-secret")
    get_settings.cache_clear()
    app = FastAPI()
    app.include_router(router, prefix="/api")
    client = TestClient(app)

    denied = client.post("/api/forex/scanner/run")
    assert denied.status_code == 403


def test_production_like_scanner_to_web_take_trade_and_telegram(forex_database):
    from app.routes.forex import router

    created = asyncio.run(scan_forex(FakeProvider())).created[0]
    app = FastAPI()
    app.include_router(router, prefix="/api")
    client = TestClient(app)

    listing = client.get("/api/forex/signals")
    assert listing.status_code == 200
    listed = next(item for item in listing.json()["signals"] if item["id"] == created.id)
    assert listed["entry_low"] == created.entry_low
    assert listed["stop_loss"] == created.stop_loss
    assert listed["timeframe"] == "15M"

    filtered = client.get("/api/forex/signals?timeframe=4H")
    assert filtered.status_code == 200
    assert filtered.json()["signals"] == []

    generic_active = client.get("/api/signals?timeframe=15M")
    assert generic_active.status_code == 200
    assert all(item["timeframe"] == "15M" for item in generic_active.json()["signals"])

    detail = client.get(f"/api/forex/signals/{created.id}")
    assert detail.status_code == 200
    assert detail.json()["take_profit_2"] == created.take_profit_2

    prepared = client.post(
        f"/api/forex/signals/{created.id}/take-trade",
        headers={"Authorization": "Bearer production-like-test-session"},
        json={
            "account_balance": 25_000,
            "risk_percentage": 0.5,
            "execution_method": "Manual broker",
        },
    )
    assert prepared.status_code == 200
    assert prepared.json()["signal"]["id"] == created.id
    assert prepared.json()["risk_amount"] == 125

    queue_dispatches(created.id, ["9001"])
    bot = FakeBot()
    delivery = asyncio.run(dispatch_pending_forex(bot))
    assert delivery["delivered"] == 1
    assert created.id in bot.messages[0]["text"]
    assert f"{created.entry_low:g} - {created.entry_high:g}" in bot.messages[0]["text"]
