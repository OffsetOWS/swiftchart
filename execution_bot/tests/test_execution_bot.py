from __future__ import annotations

from datetime import datetime, timedelta, timezone
import asyncio
import logging

import httpx
import pytest

from execution_bot.config import ExecutionSettings
from execution_bot.exchanges.hyperliquid import HyperliquidExecutionExchange, HyperliquidRateLimited
from execution_bot.indicators import atr
from execution_bot.market_filter import evaluate_market
from execution_bot.models import Candle, MarketSnapshot, SignalDecision, SignalIn
from execution_bot.risk import build_execution_plan, risk_percent_for_signal, take_profit_levels
from execution_bot.safety import validate_market_prechecks, validate_plan_prechecks
from execution_bot.security import signature_for
from execution_bot.service import preflight_signal
from execution_bot.service import _planning_balance
from execution_bot.storage import claim_webhook_nonce, init_db
from execution_bot.telegram_bot import format_trade_alert


@pytest.fixture()
def settings(tmp_path, monkeypatch):
    db_path = tmp_path / "execution.db"
    monkeypatch.setenv("EXECUTION_DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("EXECUTION_EXCHANGE", "mock")
    from execution_bot.config import get_execution_settings

    get_execution_settings.cache_clear()
    import execution_bot.storage as storage

    storage._INITIALIZED = False
    init_db()
    return get_execution_settings()


def sample_candles(count: int = 80) -> list[Candle]:
    candles: list[Candle] = []
    price = 100.0
    now = datetime.now(timezone.utc)
    for index in range(count):
        price += 0.35
        candles.append(
            Candle(
                timestamp=now - timedelta(minutes=(count - index) * 15),
                open=price - 0.25,
                high=price + 0.8,
                low=price - 0.9,
                close=price,
                volume=1000 + index * 10,
            )
        )
    return candles


def test_signal_validation_rejects_low_confidence(settings):
    signal = SignalIn(pair="BTC", side="BUY", entry=94500, confidence=50, timeframe="15m")
    accepted, reason = preflight_signal(signal)
    assert accepted is False
    assert "below minimum" in reason


def test_signal_validation_rejects_expired(settings):
    signal = SignalIn(
        pair="BTC",
        side="BUY",
        entry=94500,
        confidence=90,
        timeframe="15m",
        created_at=datetime.now(timezone.utc) - timedelta(hours=2),
    )
    accepted, reason = preflight_signal(signal)
    assert accepted is False
    assert "expired" in reason


def test_small_runtime_balance_does_not_trigger_daily_loss_limit(settings, monkeypatch):
    from execution_bot.storage import set_account_balance

    set_account_balance(11)
    signal = SignalIn(pair="BTC", side="BUY", entry=94500, confidence=90, timeframe="15m")

    accepted, reason = preflight_signal(signal)

    assert accepted is True
    assert reason == "Preflight passed."


def test_zero_synced_balance_uses_starting_balance_for_planning(settings):
    from execution_bot.storage import set_account_balance

    set_account_balance(0)

    assert _planning_balance() == settings.starting_balance


def test_zero_balance_sync_does_not_overwrite_positive_runtime_balance(settings):
    from execution_bot.storage import account_balance, set_account_balance

    set_account_balance(11)
    set_account_balance(0)

    assert account_balance() == 11


def test_risk_reduces_after_three_losses(settings):
    assert risk_percent_for_signal(96, 0, settings) == 5
    assert risk_percent_for_signal(96, 3, settings) == 2.5


def test_stop_loss_uses_wider_structure_stop(settings):
    candles = sample_candles()
    signal = SignalIn(pair="BTC", side="BUY", entry=candles[-1].close, confidence=90, timeframe="15m")
    atr_value = atr(candles, 14)
    plan = build_execution_plan(signal, candles, 100, 0, 0, atr_value, atr_value / signal.entry * 100, "trending", settings)
    assert plan.stop_loss < signal.entry
    assert plan.stop_distance == pytest.approx(signal.entry - plan.stop_loss)


def test_leverage_caps_and_reduces_position(settings):
    custom = ExecutionSettings(max_leverage=1, max_exposure_per_coin_percent=20, execution_database_url=settings.execution_database_url)
    candles = sample_candles()
    signal = SignalIn(pair="BTC", side="BUY", entry=100, confidence=99, timeframe="15m")
    plan = build_execution_plan(signal, candles, 100, 0, 0, 1, 1, "trending", custom)
    assert plan.leverage <= 1
    assert plan.notional_value <= 20
    assert plan.risk_amount <= 5


def test_conservative_risk_cap_stays_at_half_percent(settings):
    custom = ExecutionSettings(
        base_risk_percent=0.5,
        max_risk_percent=0.5,
        max_leverage=3,
        execution_database_url=settings.execution_database_url,
    )

    assert risk_percent_for_signal(99, 0, custom) == 0.5
    assert custom.max_leverage == 3


def test_tiny_account_can_allow_ten_dollar_hyperliquid_notional(settings):
    custom = ExecutionSettings(
        starting_balance=11,
        max_exposure_per_coin_percent=100,
        max_leverage=3,
        execution_database_url=settings.execution_database_url,
    )

    assert custom.starting_balance * (custom.max_exposure_per_coin_percent / 100) >= custom.min_order_notional


def test_hyperliquid_address_is_not_used_as_signing_secret(settings):
    custom = ExecutionSettings(
        hyperliquid_api_key="0x9c6500000000000000000000000000000000f070",
        hyperliquid_api_secret="",
        hyperliquid_private_key="",
        execution_database_url=settings.execution_database_url,
    )

    assert custom.effective_hyperliquid_signing_secret == ""


def test_hyperliquid_account_summary_uses_cache(settings, monkeypatch):
    async def run():
        HyperliquidExecutionExchange.clear_account_cache()
        exchange = HyperliquidExecutionExchange()
        exchange.settings = settings.model_copy(update={"hyperliquid_account_cache_ttl_seconds": 60})
        calls = 0

        async def load_state(address, key):
            nonlocal calls
            calls += 1
            return {"marginSummary": {"accountValue": "123.45"}, "assetPositions": []}

        monkeypatch.setattr(exchange, "_load_account_state", load_state)

        first = await exchange._account_state("0xabc")
        second = await exchange._account_state("0xabc")

        assert first == second
        assert calls == 1

    asyncio.run(run())


def test_hyperliquid_429_sets_backoff_and_raises_rate_limit(settings, monkeypatch):
    async def run():
        HyperliquidExecutionExchange.clear_account_cache()
        exchange = HyperliquidExecutionExchange()
        exchange.settings = settings.model_copy(update={"hyperliquid_rate_limit_cooldown_seconds": 30})
        sleeps = []

        async def fake_sleep(seconds):
            sleeps.append(seconds)

        async def operation():
            request = httpx.Request("POST", "https://api.hyperliquid.xyz/info")
            response = httpx.Response(429, request=request)
            raise httpx.HTTPStatusError("rate limited", request=request, response=response)

        monkeypatch.setattr("execution_bot.exchanges.hyperliquid.asyncio.sleep", fake_sleep)

        with pytest.raises(HyperliquidRateLimited):
            await exchange._retry_async(operation, attempts=3, account_cache_key=("https://api.hyperliquid.xyz", "0xabc"))

        assert sleeps == [1, 2]
        assert exchange._account_rate_limited_until[("https://api.hyperliquid.xyz", "0xabc")] > 0

    asyncio.run(run())


def test_secure_logging_redacts_tokens_and_user_tuning_params():
    from execution_bot.security import RedactingFilter, redact_sensitive

    message = (
        'GET /api/analyze?symbol=BTCUSDT&account_size=10000&risk_per_trade_pct=1&min_rr=2 '
        'Authorization: Bearer ey.secret.token api_key="abc123" private_key=0x'
        + "a" * 64
    )

    redacted = redact_sensitive(message)

    assert "account_size=10000" not in redacted
    assert "risk_per_trade_pct=1" not in redacted
    assert "min_rr=2" not in redacted
    assert "abc123" not in redacted
    assert "a" * 64 not in redacted
    assert "[REDACTED]" in redacted

    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        "",
        0,
        '%s - "%s" %s',
        ("127.0.0.1:1234", "GET /api/analyze?account_size=10000&risk_per_trade_pct=1 HTTP/1.1", 200),
        None,
    )
    assert RedactingFilter().filter(record) is True
    assert len(record.args) == 3
    assert "account_size=10000" not in record.args[1]
    assert "risk_per_trade_pct=1" not in record.args[1]


def test_strategy_regression_core_risk_and_targets_unchanged(settings):
    signal = SignalIn(pair="BTC", side="BUY", entry=100, confidence=96, timeframe="15m")
    targets = take_profit_levels(signal, 10)

    assert risk_percent_for_signal(96, 0, settings) == 5
    assert risk_percent_for_signal(96, 3, settings) == 2.5
    assert [target["target"] for target in targets] == [110, 120, 130]
    assert [target["close_percent"] for target in targets] == [40, 30, 30]


def test_take_profit_levels_are_r_based():
    signal = SignalIn(pair="BTC", side="SELL", entry=100, confidence=90, timeframe="15m")
    targets = take_profit_levels(signal, 10)
    assert [target["target"] for target in targets] == [90, 80, 70]
    assert [target["close_percent"] for target in targets] == [40, 30, 30]


def test_market_filter_allows_low_adx_chop_as_context(settings):
    flat = [
        Candle(open=100, high=100.1, low=99.9, close=100, volume=1000, timestamp=datetime.now(timezone.utc) - timedelta(minutes=i))
        for i in range(40)
    ]
    result = evaluate_market(MarketSnapshot(candles=flat, bid=99.99, ask=100.01), settings)
    assert result.allowed is True
    assert result.condition in {"choppy", "compression"}


def test_market_filter_ignores_current_incomplete_candle_volume(settings):
    candles = sample_candles(80)
    candles.append(
        Candle(
            timestamp=datetime.now(timezone.utc),
            open=candles[-1].close,
            high=candles[-1].close + 0.1,
            low=candles[-1].close - 0.1,
            close=candles[-1].close,
            volume=1,
        )
    )

    result = evaluate_market(MarketSnapshot(candles=candles, bid=candles[-1].close * 0.9995, ask=candles[-1].close * 1.0005), settings)

    assert result.allowed is True
    assert result.volume_ratio > settings.min_volume_ratio
    assert "Volume is too weak." not in result.reasons


def test_execution_signature_and_nonce_replay_protection(settings):
    body = b'{"pair":"BTCUSDT"}'
    signature = signature_for("x" * 32, "1710000000", "nonce-1", body)

    assert signature == signature_for("x" * 32, "1710000000", "nonce-1", body)
    assert signature != signature_for("x" * 32, "1710000000", "nonce-2", body)
    assert claim_webhook_nonce("nonce-1", 1710000000, 900) is True
    assert claim_webhook_nonce("nonce-1", 1710000000, 900) is False


def test_execution_prechecks_reject_low_volume_and_unlisted_symbol(settings):
    signal = SignalIn(pair="DOGE", side="BUY", entry=100, confidence=90, timeframe="15m")
    snapshot = MarketSnapshot(candles=sample_candles(), bid=99.95, ask=100.05, mark_price=100, perp_volume_24h=50_000)

    reasons = validate_market_prechecks(signal, "DOGEUSDT", snapshot, settings)

    assert any("EXECUTION_SYMBOL_ALLOWLIST" in reason for reason in reasons)
    assert any("volume" in reason.lower() for reason in reasons)


def test_execution_plan_prechecks_reject_excessive_risk(settings):
    candles = sample_candles()
    signal = SignalIn(pair="BTC", side="BUY", entry=100, confidence=99, timeframe="15m")
    plan = build_execution_plan(signal, candles, 100, 0, 0, 1, 1, "trending", settings)
    strict = settings.model_copy(update={"max_risk_per_trade_percent": 0.1})

    assert any("Risk" in reason for reason in validate_plan_prechecks(plan, strict))


def test_rejected_signals_format_telegram_alert(settings):
    signal = SignalIn(pair="BTC", side="BUY", entry=100, confidence=50, timeframe="15m")
    decision = SignalDecision(accepted=False, reason="Rejected", signal=signal)

    assert "SwiftChart Signal Rejected" in format_trade_alert(decision)
    assert "Reason: Rejected" in format_trade_alert(decision)
