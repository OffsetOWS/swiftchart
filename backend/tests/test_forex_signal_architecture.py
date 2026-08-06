from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
import sqlite3
from types import SimpleNamespace

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
from app.forex.scanner import (
    _entry_confirmation,
    _entry_quality,
    _pending_retest_confirmed,
    _retest_confirmed,
    analyze_forex_timeframe,
    scan_forex,
)
from app.forex.storage import (
    get_scanner_diagnostics,
    get_signal,
    list_signals,
    queue_dispatches,
    promote_retest_signal,
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
        # End with a completed pullback and confirmation instead of a fresh
        # range-high breakout, so this fixture represents an approved entry.
        rows[-2].update(open=1.0820, high=1.0824, low=1.0783, close=1.0788)
        rows[-1].update(open=1.0788, high=1.0808, low=1.0786, close=1.0805)
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
    ("timeframe", "expected_timeframes", "bias_timeframe"),
    [
        ("1H", {"1h", "4h"}, "4h"),
        ("4H", {"4h", "1d"}, "1d"),
        ("1D", {"1d"}, "1d"),
    ],
)
def test_each_timeframe_uses_real_higher_timeframe_and_persists_immutable_signal(
    forex_database,
    timeframe,
    expected_timeframes,
    bias_timeframe,
):
    provider = FakeProvider()
    first = asyncio.run(scan_forex(provider, timeframe=timeframe))
    assert first.created
    assert set(provider.timeframes) == expected_timeframes

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
    assert signal.bias_timeframe == bias_timeframe
    assert signal.setup_timeframe == timeframe.lower()
    assert signal.execution_timeframe == timeframe.lower()
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


def _regression_trend_frame(direction: str, *, step: float = 0.00008) -> pd.DataFrame:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    rows = []
    for index in range(90):
        close = 1.4 + (step * index if direction == "LONG" else -step * index)
        open_ = close - 0.00022 if direction == "LONG" else close + 0.00022
        rows.append({
            "timestamp": start + timedelta(hours=index),
            "open": open_,
            "high": max(open_, close) + 0.00012,
            "low": min(open_, close) - 0.00012,
            "close": close,
            "volume": 1_000,
        })
    return pd.DataFrame(rows)


def test_usdcad_short_4h_waits_when_support_blocks_tp1(monkeypatch):
    frame = _regression_trend_frame("SHORT")
    price = float(frame.iloc[-1]["close"])
    atr = float((frame["high"] - frame["low"]).tail(14).mean())
    monkeypatch.setattr(
        "app.forex.scanner._entry_confirmation",
        lambda *_args, **_kwargs: (True, "Confirmed bearish retest.", "retest", price),
    )
    monkeypatch.setattr(
        "app.forex.scanner._swing_levels",
        lambda *_args, **_kwargs: [price - atr * 0.45],
    )
    plan, audit = analyze_forex_timeframe(
        SUPPORTED_FOREX_PAIRS["USDCAD"],
        frame,
        htf_candles=frame,
        timeframe="4H",
        scan_id="usdcad-regression",
        session_label="New York",
        news_risk="LOW",
        now=datetime(2026, 8, 6, tzinfo=UTC),
    )
    assert audit["decision"] == "WAIT_FOR_RETEST"
    assert plan is not None and plan["status"] == "WAIT_FOR_RETEST"
    assert "opposing structure" in plan["setup_reason"]


def test_cadchf_long_1d_waits_at_end_of_recent_range():
    quality = _entry_quality(
        _regression_trend_frame("LONG"),
        "LONG",
        entry_ok=True,
        trigger_type="sweep",
        retest_level=1.4,
    )
    assert quality["hard_gate"] is True
    assert any("end of its recent range" in reason for reason in quality["reasons"])


def test_nzdcad_short_1h_waits_after_extended_impulse():
    quality = _entry_quality(
        _regression_trend_frame("SHORT", step=0.00018),
        "SHORT",
        entry_ok=True,
        trigger_type="continuation",
        retest_level=1.39,
    )
    assert quality["hard_gate"] is True
    assert any("overextended" in reason for reason in quality["reasons"])
    assert any("strong directional candles" in reason for reason in quality["reasons"])


def test_retest_requires_a_later_completed_hold():
    frame = _regression_trend_frame("LONG")
    setup_time = frame.iloc[-1]["timestamp"].to_pydatetime()
    level = float(frame.iloc[-1]["close"])
    assert not _retest_confirmed(frame, "LONG", level, setup_time)

    later = frame.iloc[-1].copy()
    later["timestamp"] = pd.Timestamp(setup_time + timedelta(hours=1))
    later["open"] = level - 0.0001
    later["low"] = level - 0.00015
    later["high"] = level + 0.00035
    later["close"] = level + 0.00025
    confirmed = pd.concat([frame, pd.DataFrame([later])], ignore_index=True)
    assert _retest_confirmed(confirmed, "LONG", level, setup_time)


def test_breakout_requires_displacement_then_a_later_follow_through():
    frame = _regression_trend_frame("LONG")
    breakout_level = float(frame.iloc[-12:-2]["high"].max())
    first_breakout = frame.iloc[-1].copy()
    first_breakout["open"] = breakout_level - 0.0001
    first_breakout["low"] = breakout_level - 0.00015
    first_breakout["close"] = breakout_level + 0.0007
    first_breakout["high"] = breakout_level + 0.0008
    frame.iloc[-1] = first_breakout

    confirmed, reason, trigger_type, level = _entry_confirmation(frame, "LONG", "1H")
    assert confirmed is False
    assert trigger_type == "breakout"
    assert "later retest or follow-through" in reason
    assert level == pytest.approx(breakout_level)

    follow_through = first_breakout.copy()
    follow_through["timestamp"] = pd.Timestamp(first_breakout["timestamp"]) + timedelta(hours=1)
    follow_through["open"] = float(first_breakout["close"]) - 0.00005
    follow_through["low"] = float(follow_through["open"]) - 0.00005
    follow_through["close"] = float(first_breakout["close"]) + 0.00025
    follow_through["high"] = float(follow_through["close"]) + 0.00005
    followed = pd.concat([frame, pd.DataFrame([follow_through])], ignore_index=True)

    confirmed, reason, trigger_type, level = _entry_confirmation(followed, "LONG", "1H")
    assert confirmed is True
    assert trigger_type == "breakout"
    assert "displacement and follow-through" in reason
    assert level == pytest.approx(float(followed.tail(12).iloc[:-2]["high"].max()))


def test_weak_breakout_cannot_promote_on_retest_alone():
    frame = _regression_trend_frame("LONG")
    breakout_level = float(frame.iloc[-12:-2]["high"].max())
    setup = frame.iloc[-1].copy()
    setup["open"] = breakout_level - 0.00002
    setup["low"] = breakout_level - 0.00005
    setup["close"] = breakout_level + 0.00003
    setup["high"] = breakout_level + 0.00006
    frame.iloc[-1] = setup
    confirmed, reason, _, level = _entry_confirmation(frame, "LONG", "1H")
    assert confirmed is False
    assert "lacks meaningful displacement" in reason

    retest = setup.copy()
    retest["timestamp"] = pd.Timestamp(setup["timestamp"]) + timedelta(hours=1)
    retest["open"] = breakout_level - 0.00002
    retest["low"] = breakout_level - 0.00008
    retest["close"] = breakout_level + 0.00008
    retest["high"] = breakout_level + 0.00012
    followed = pd.concat([frame, pd.DataFrame([retest])], ignore_index=True)
    pending = SimpleNamespace(
        retest_level=level,
        setup_candle_time=pd.Timestamp(setup["timestamp"]).to_pydatetime(),
        entry_trigger=reason,
    )
    assert not _pending_retest_confirmed(followed, "LONG", pending)


def test_waiting_signal_is_not_telegram_eligible_until_promoted(forex_database):
    import app.routes.forex as routes
    from fastapi import HTTPException
    from bot.storage import add_subscriber

    signal = asyncio.run(scan_forex(FakeProvider(), timeframe="1H")).created[0]
    waiting = update_signal_market_state(
        signal.id,
        status="WAIT_FOR_RETEST",
        price=signal.entry_price,
        checked_at=signal.created_at,
    )
    add_subscriber(7001)
    assert enqueue_forex_signal(waiting) == 0
    queue_dispatches(waiting.id, ["7001"])
    bot = FakeBot()
    suppressed = asyncio.run(dispatch_pending_forex(bot))
    assert all(
        not message["text"].startswith(f"<b>{waiting.symbol} {waiting.direction}</b>")
        for message in bot.messages
    )
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            routes.take_forex_trade(
                waiting.id,
                TakeTradeRequest(
                    account_balance=10_000,
                    risk_percentage=1,
                    execution_method="Copy setup",
                ),
                authorization="Bearer valid-test-session-token",
            )
        )
    assert exc.value.status_code == 409

    confirmed_at = signal.created_at + timedelta(hours=1)
    promoted = promote_retest_signal(
        waiting.id,
        entry_price=waiting.entry_price,
        entry_low=waiting.entry_low,
        entry_high=waiting.entry_high,
        stop_loss=waiting.stop_loss,
        take_profit_1=waiting.take_profit_1,
        take_profit_2=waiting.take_profit_2,
        risk_reward_1=waiting.risk_reward_1,
        risk_reward_2=waiting.risk_reward_2,
        entry_trigger="Later completed retest held.",
        entry_quality_score=90,
        setup_score=88,
        grade="A",
        technical_score=88,
        context_adjustment=0,
        cross_market_context=waiting.cross_market_context,
        setup_reason="Approved only after a later completed retest.",
        confirmed_at=confirmed_at,
    )
    assert promoted.status == "PENDING_ENTRY"
    assert promoted.retest_confirmed_at == confirmed_at
    assert enqueue_forex_signal(promoted) == 1


def test_rsi_is_context_only_and_does_not_change_setup_score(monkeypatch):
    frame = _regression_trend_frame("LONG")
    price = float(frame.iloc[-1]["close"])
    monkeypatch.setattr(
        "app.forex.scanner._entry_confirmation",
        lambda *_args, **_kwargs: (True, "Confirmed bullish retest.", "retest", price),
    )
    monkeypatch.setattr("app.forex.scanner._swing_levels", lambda *_args, **_kwargs: [])
    kwargs = {
        "pair": SUPPORTED_FOREX_PAIRS["CADCHF"],
        "candles": frame,
        "htf_candles": None,
        "timeframe": "1D",
        "scan_id": "rsi-context-regression",
        "session_label": "London",
        "news_risk": "LOW",
        "now": datetime(2026, 8, 6, tzinfo=UTC),
    }

    monkeypatch.setattr("app.forex.scanner._rsi", lambda *_args: 99.0)
    overbought, _ = analyze_forex_timeframe(**kwargs)
    monkeypatch.setattr("app.forex.scanner._rsi", lambda *_args: 1.0)
    oversold, _ = analyze_forex_timeframe(**kwargs)

    assert overbought is not None and oversold is not None
    assert overbought["setup_score"] == oversold["setup_score"]
    assert overbought["direction"] == oversold["direction"] == "LONG"
