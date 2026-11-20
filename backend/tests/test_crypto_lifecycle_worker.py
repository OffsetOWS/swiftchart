from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.services import crypto_lifecycle


def test_crypto_lifecycle_loop_checks_immediately_after_startup_delay(monkeypatch):
    calls: list[str] = []
    sleeps: list[int] = []

    async def fake_check_trade_outcomes():
        calls.append("checked")
        return {"checked": 7, "updated": 5, "errors": 0, "markets_fetched": 2}

    async def fake_sleep(seconds: int):
        sleeps.append(seconds)
        if len(sleeps) > 1:
            raise asyncio.CancelledError

    monkeypatch.setattr(
        crypto_lifecycle,
        "get_settings",
        lambda: SimpleNamespace(
            crypto_lifecycle_startup_delay_seconds=3,
            crypto_lifecycle_interval_seconds=120,
        ),
    )
    monkeypatch.setattr(crypto_lifecycle, "check_trade_outcomes", fake_check_trade_outcomes)
    monkeypatch.setattr(crypto_lifecycle.asyncio, "sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(crypto_lifecycle._crypto_lifecycle_loop())

    assert calls == ["checked"]
    assert sleeps == [3, 120]


def test_start_crypto_lifecycle_worker_is_idempotent(monkeypatch):
    started = asyncio.Event()
    release = asyncio.Event()

    async def fake_loop():
        started.set()
        await release.wait()

    monkeypatch.setattr(
        crypto_lifecycle,
        "get_settings",
        lambda: SimpleNamespace(crypto_lifecycle_enabled=True),
    )
    monkeypatch.setattr(crypto_lifecycle, "_crypto_lifecycle_loop", fake_loop)
    monkeypatch.setattr(crypto_lifecycle, "_background_task", None)

    async def run_case():
        first = crypto_lifecycle.start_crypto_lifecycle_worker()
        second = crypto_lifecycle.start_crypto_lifecycle_worker()
        assert first is not None
        assert first is second
        await started.wait()
        release.set()
        await first

    asyncio.run(run_case())


def test_start_crypto_lifecycle_worker_respects_disabled_setting(monkeypatch):
    monkeypatch.setattr(
        crypto_lifecycle,
        "get_settings",
        lambda: SimpleNamespace(crypto_lifecycle_enabled=False),
    )
    monkeypatch.setattr(crypto_lifecycle, "_background_task", None)

    assert crypto_lifecycle.start_crypto_lifecycle_worker() is None
