from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
import sqlite3

import pandas as pd
import pytest
import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import get_settings
from app.forex.config import SUPPORTED_FOREX_PAIRS, enabled_forex_timeframes
from app.forex.lifecycle import next_signal_status
from app.forex import storage as forex_storage
from app.forex.models import TakeTradeRequest
from app.forex.providers import (
    ForexDataProvider,
    ForexProviderQuotaExceeded,
    TwelveDataForexProvider,
)
from app.forex.scanner import scan_forex
from app.forex.storage import (
    get_scanner_diagnostics,
    get_signal,
    list_signals,
    queue_dispatches,
    update_signal_market_state,
)
from app.forex.telegram import dispatch_pending_forex, enqueue_forex_signal, format_forex_signal


class FakeProvider(ForexDataProvider):
    name = "fake"

    def __init__(self) -> None:
        self.timeframes: list[str] = []

    async def candles(self, pair, timeframe: str, limit: int = 240) -> pd.DataFrame:
        self.timeframes.append(timeframe)
        start = datetime(2025, 1, 1, tzinfo=UTC)
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


class NoTradeProvider(FakeProvider):
    async def candles(self, pair, timeframe: str, limit: int = 240) -> pd.DataFrame:
        self.timeframes.append(timeframe)
        start = datetime(2025, 1, 1, tzinfo=UTC)
        spacing = {"15m": 15, "1h": 60, "4h": 240, "1d": 1440}[timeframe]
        return pd.DataFrame(
            [
                {
                    "timestamp": start + timedelta(minutes=spacing * index),
                    "open": 1.0,
                    "high": 1.0004,
                    "low": 0.9996,
                    "close": 1.0,
                    "volume": 1_000,
                }
                for index in range(90)
            ]
        )


class QuotaProvider(FakeProvider):
    async def candles(self, pair, timeframe: str, limit: int = 240) -> pd.DataFrame:
        self.timeframes.append(timeframe)
        raise ForexProviderQuotaExceeded(
            "Forex market-data daily credit limit reached; scanning resumes after the provider reset."
        )


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


def test_daily_quota_response_opens_provider_circuit(monkeypatch):
    import app.forex.providers as providers

    requests = 0

    class QuotaClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, *args, **kwargs):
            nonlocal requests
            requests += 1
            return httpx.Response(
                429,
                request=httpx.Request("GET", "https://api.twelvedata.com/time_series"),
                json={"message": "You have run out of API credits for the day."},
            )

    monkeypatch.setattr(providers.httpx, "AsyncClient", lambda **kwargs: QuotaClient())
    TwelveDataForexProvider._unavailable_until = None
    TwelveDataForexProvider._unavailable_reason = None
    provider = TwelveDataForexProvider(api_key="test-key")
    pair = SUPPORTED_FOREX_PAIRS["EURUSD"]

    with pytest.raises(ForexProviderQuotaExceeded, match="daily credit limit"):
        asyncio.run(provider.candles(pair, "15m", 2))
    with pytest.raises(ForexProviderQuotaExceeded, match="daily credit limit"):
        asyncio.run(provider.candles(pair, "15m", 2))

    assert requests == 1
    TwelveDataForexProvider._unavailable_until = None
    TwelveDataForexProvider._unavailable_reason = None


def test_quota_failure_stops_scan_after_first_pair(forex_database):
    provider = QuotaProvider()
    result = asyncio.run(scan_forex(provider, timeframe="1H", trigger_source="manual"))

    assert result.result_status == "FAILED"
    assert result.pairs_scanned == 1
    assert result.persisted_count == 0
    assert len(provider.timeframes) == 1
    assert not list_signals(("PENDING_ENTRY",))
    diagnostics = get_scanner_diagnostics()
    assert diagnostics["latest_scanner_error"]
    assert "daily credit limit" in diagnostics["latest_scanner_error"]


def test_signal_list_does_not_call_scanner(monkeypatch, forex_database):
    import app.routes.forex as routes

    async def fail_scan():
        raise AssertionError("GET /forex/signals must never call the scanner")

    monkeypatch.setattr(routes, "scan_forex", fail_scan)
    response = asyncio.run(routes.forex_signals(status_filter=None, limit=100))
    assert response.count == 0


@pytest.mark.parametrize(
    ("timeframe", "provider_timeframe"),
    [("1H", "1h"), ("4H", "4h"), ("1D", "1d")],
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
    assert list_signals(("PENDING_ENTRY",), timeframe=timeframe)


def test_timeframes_coexist_and_dedupe_only_within_same_timeframe(forex_database):
    provider = FakeProvider()
    created = {}
    for timeframe in enabled_forex_timeframes():
        result = asyncio.run(scan_forex(provider, timeframe=timeframe))
        assert result.created
        created[timeframe] = result.created[0]

    assert len({signal.id for signal in created.values()}) == 4
    assert len({signal.dedupe_key for signal in created.values()}) == 4
    assert {signal.timeframe for signal in list_signals(("PENDING_ENTRY",))} == set(
        enabled_forex_timeframes()
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
    assert message.splitlines()[0] == f"<b>{signal.symbol} {signal.direction}</b>"
    assert "SwiftChart Forex Signal" not in message
    assert f"#{signal.id}" not in message
    assert f"/app/signal/{signal.id}" in message
    precision = 3 if signal.symbol.endswith("JPY") else 5
    assert f"{signal.entry_low:.{precision}f} - {signal.entry_high:.{precision}f}" in message
    assert f"{signal.stop_loss:.{precision}f}" in message
    assert f"{signal.take_profit_1:.{precision}f}" in message
    assert f"{signal.take_profit_2:.{precision}f}" in message
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
    assert any(f"/app/signal/{signal.id}" in item["text"] for item in bot.messages)


@pytest.mark.parametrize("timeframe", ["1H", "4H", "1D"])
def test_telegram_keeps_supported_timeframe_and_compact_template(
    forex_database,
    timeframe,
):
    signal = asyncio.run(scan_forex(FakeProvider(), timeframe=timeframe)).created[0]
    message = format_forex_signal(signal, "https://swiftchart.xyz")
    assert message.startswith(f"<b>{signal.symbol} {signal.direction}</b>\n")
    assert f"Timeframe: {timeframe}" in message
    assert f' href="https://swiftchart.xyz/app/signal/{signal.id}"' in message
    assert "Strategy:" not in message
    assert "Expires:" not in message


def test_15m_signal_is_persisted_but_not_queued_for_telegram(forex_database):
    from bot.storage import add_subscriber

    add_subscriber(123)
    signal = asyncio.run(scan_forex(FakeProvider(), timeframe="15M")).created[0]

    assert signal.timeframe == "15M"
    assert get_signal(signal.id) is not None
    assert enqueue_forex_signal(signal) == 0

    bot = FakeBot()
    result = asyncio.run(dispatch_pending_forex(bot))
    assert result == {"attempted": 0, "delivered": 0, "failed": 0}
    assert bot.messages == []


def test_no_trade_manual_scan_records_history_without_signal(forex_database):
    from app.utils.database import get_connection

    result = asyncio.run(
        scan_forex(NoTradeProvider(), timeframe="1H", trigger_source="manual")
    )
    assert result.result_status == "NO_TRADE"
    assert result.timeframe == "1H"
    assert result.trigger_source == "manual"
    assert result.pairs_scanned == len(SUPPORTED_FOREX_PAIRS)
    assert result.persisted_count == 0
    assert result.rejection_reasons
    assert list_signals() == []
    with get_connection() as connection:
        row = connection.execute(
            "SELECT * FROM forex_scan_runs WHERE id = ?", (result.scan_id,)
        ).fetchone()
    assert row["trigger_source"] == "manual"
    assert row["result_status"] == "NO_TRADE"
    assert row["rejected_count"] == len(SUPPORTED_FOREX_PAIRS)


def test_manual_endpoint_runs_selected_timeframe_and_returns_persisted_signal(
    monkeypatch,
    forex_database,
):
    import app.forex.scanner as scanner
    import app.routes.forex as routes
    from app.routes.forex import router

    monkeypatch.setattr(scanner, "get_forex_provider", lambda: FakeProvider())
    routes._manual_scans_active.clear()
    routes._manual_scan_completed_at.clear()
    app = FastAPI()
    app.include_router(router, prefix="/api")
    client = TestClient(app)
    response = client.post(
        "/api/forex/scan?timeframe=4H",
        headers={"Authorization": "Bearer valid-manual-session-token"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["timeframe"] == "4H"
    assert payload["trigger_source"] == "manual"
    assert payload["result_status"] == "TRADE_FOUND"
    assert payload["created"]
    assert get_signal(payload["created"][0]["id"]) is not None

    cooldown = client.post(
        "/api/forex/scan?timeframe=4H",
        headers={"Authorization": "Bearer valid-manual-session-token"},
    )
    assert cooldown.status_code == 429


def test_manual_and_scheduled_paths_share_the_canonical_scanner():
    import app.forex.scanner as scanner
    import app.forex.scheduler as scheduler
    import app.routes.forex as routes

    assert routes.scan_forex is scanner.scan_forex
    assert scheduler.scan_forex is scanner.scan_forex


def test_scanner_diagnostics_reports_latest_scan_and_outbox(forex_database):
    signal = asyncio.run(
        scan_forex(FakeProvider(), timeframe="1H", trigger_source="scheduled")
    ).created[0]
    queue_dispatches(signal.id, ["8080"])
    diagnostics = get_scanner_diagnostics()
    assert diagnostics["last_scan_timeframe"] == "1H"
    assert diagnostics["last_trigger_source"] == "scheduled"
    assert diagnostics["pairs_evaluated"] == len(SUPPORTED_FOREX_PAIRS)
    assert diagnostics["candidates_found"] > 0
    assert diagnostics["persisted"] > 0
    assert diagnostics["telegram_queued"] == 1


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
    assert tp1 == "TP1_HIT_TP2_RUNNING"

    full_close_signal = open_signal.model_copy(update={"tp1_closes_position": True})
    full_close, _, full_close_at = next_signal_status(
        full_close_signal,
        price=tp1_price,
        checked_at=signal.created_at + timedelta(hours=1),
    )
    assert full_close == "TP1_HIT"
    assert full_close_at == signal.created_at + timedelta(hours=1)

    expired, _, expired_at = next_signal_status(
        signal,
        price=signal.entry_high + 10,
        checked_at=signal.expires_at,
    )
    assert expired == "EXPIRED"
    assert expired_at == signal.expires_at


def test_lifecycle_persists_live_price_activation_and_hit_timestamps_without_mutating_plan(
    forex_database,
):
    signal = asyncio.run(scan_forex(FakeProvider(), timeframe="1H")).created[0]
    original_plan = (
        signal.entry_low,
        signal.entry_high,
        signal.stop_loss,
        signal.take_profit_1,
        signal.take_profit_2,
        signal.created_at,
    )
    opened_at = signal.created_at + timedelta(minutes=30)
    opened = update_signal_market_state(
        signal.id,
        status="OPEN",
        price=signal.entry_price,
        checked_at=opened_at,
        activated_at=opened_at,
    )
    assert opened.latest_price == signal.entry_price
    assert opened.latest_price_at == opened_at
    assert opened.activated_entry_price == signal.entry_price
    assert opened.latest_price_at != opened.created_at

    tp1_at = opened_at + timedelta(hours=1)
    tp1 = update_signal_market_state(
        signal.id,
        status="TP1_HIT_TP2_RUNNING",
        price=signal.take_profit_1,
        checked_at=tp1_at,
    )
    assert tp1.status == "TP1_HIT_TP2_RUNNING"
    assert tp1.tp1_hit_at == tp1_at
    assert tp1.tp2_hit_at is None

    tp2_at = tp1_at + timedelta(hours=1)
    completed = update_signal_market_state(
        signal.id,
        status="TP2_HIT",
        price=signal.take_profit_2,
        checked_at=tp2_at,
        closed_at=tp2_at,
    )
    assert completed.tp1_hit_at == tp1_at
    assert completed.tp2_hit_at == tp2_at
    assert completed.closed_at == tp2_at
    assert (
        completed.entry_low,
        completed.entry_high,
        completed.stop_loss,
        completed.take_profit_1,
        completed.take_profit_2,
        completed.created_at,
    ) == original_plan


def test_schema_migrates_legacy_running_tp1_state(forex_database):
    signal = asyncio.run(scan_forex(FakeProvider(), timeframe="1H")).created[0]
    update_signal_market_state(
        signal.id, status="TP1_HIT", price=signal.take_profit_1,
        checked_at=signal.created_at + timedelta(hours=1),
    )
    forex_storage._FOREX_SCHEMA_READY_FOR = None
    forex_storage.ensure_forex_schema()
    assert get_signal(signal.id).status == "TP1_HIT_TP2_RUNNING"


def test_lifecycle_persists_stopped_timestamp_and_closing_price(forex_database):
    signal = asyncio.run(scan_forex(FakeProvider(), timeframe="4H")).created[0]
    stopped_at = signal.created_at + timedelta(hours=4)
    stopped = update_signal_market_state(
        signal.id,
        status="STOPPED",
        price=signal.stop_loss,
        checked_at=stopped_at,
        closed_at=stopped_at,
    )
    assert stopped.latest_price == signal.stop_loss
    assert stopped.latest_price_at == stopped_at
    assert stopped.stopped_at == stopped_at
    assert stopped.status == "STOPPED"


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
    assert listed["timeframe"] == "1H"

    filtered = client.get("/api/forex/signals?timeframe=4H")
    assert filtered.status_code == 200
    assert filtered.json()["signals"] == []

    generic_active = client.get("/api/signals?timeframe=15M")
    assert generic_active.status_code == 200
    assert generic_active.json()["signals"] == []

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
    assert bot.messages[0]["text"].startswith(
        f"<b>{created.symbol} {created.direction}</b>\n"
    )
    assert f"/app/signal/{created.id}" in bot.messages[0]["text"]
    precision = 3 if created.symbol.endswith("JPY") else 5
    assert (
        f"{created.entry_low:.{precision}f} - {created.entry_high:.{precision}f}"
        in bot.messages[0]["text"]
    )
