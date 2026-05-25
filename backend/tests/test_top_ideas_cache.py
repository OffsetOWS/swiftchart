from __future__ import annotations

import asyncio
from time import monotonic

from app.models.schemas import TradeIdea
from app.services import scanner


def idea(symbol: str = "BTCUSDT") -> TradeIdea:
    return TradeIdea(
        symbol=symbol,
        timeframe="4h",
        exchange="hyperliquid",
        direction="Long",
        entry_zone=(100.0, 101.0),
        stop_loss=96.0,
        take_profit_1=110.0,
        take_profit_2=118.0,
        risk_reward_ratio=2.5,
        reason="Cached setup.",
        confidence_score=82,
        setup_score=82,
        invalid_condition="Close below support.",
        rank_score=82,
    )


def reset_scanner_cache() -> None:
    scanner._scan_cache.clear()
    scanner._refresh_tasks.clear()
    scanner._refresh_started_at.clear()
    scanner._refresh_finished_at.clear()
    scanner._refresh_duration_seconds.clear()


def cached_payload(symbol: str = "BTCUSDT") -> dict:
    return {
        "timeframe": "4h",
        "exchange": "hyperliquid",
        "ideas": [idea(symbol)],
        "pending_setups": [],
        "errors": [],
        "message": None,
        "scan_stats": {"valid_setups": 1, "pending_setups": 0},
    }


def test_top_ideas_returns_fresh_cache_without_running_scan(monkeypatch):
    reset_scanner_cache()
    key = ("hyperliquid", "4h")
    scanner._scan_cache[key] = (monotonic(), cached_payload())

    async def fail_scan(*args, **kwargs):
        raise AssertionError("run_scan must not run for a fresh cache")

    monkeypatch.setattr(scanner, "run_scan", fail_scan)

    result = asyncio.run(scanner.cached_top_ideas("hyperliquid", "4h"))

    assert len(result["ideas"]) == 1
    assert result["refreshing"] is False
    assert result["refresh_in_progress"] is False
    assert result["cache_age_seconds"] is not None


def test_stale_cache_returns_old_data_and_refreshing_true(monkeypatch):
    reset_scanner_cache()
    key = ("hyperliquid", "4h")
    scanner._scan_cache[key] = (monotonic() - scanner.SCAN_TTL_SECONDS - 10, cached_payload("ETHUSDT"))
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_scan(*args, **kwargs):
        started.set()
        await release.wait()
        scanner._scan_cache[key] = (monotonic(), cached_payload("SOLUSDT"))
        return scanner._scan_cache[key][1]

    async def exercise():
        monkeypatch.setattr(scanner, "run_scan", slow_scan)
        result = await scanner.cached_top_ideas("hyperliquid", "4h")
        assert result["ideas"][0].symbol == "ETHUSDT"
        assert result["refreshing"] is True
        assert result["refresh_in_progress"] is True
        await asyncio.wait_for(started.wait(), timeout=1)
        task = scanner._refresh_tasks[key]
        release.set()
        await asyncio.wait_for(task, timeout=1)

    asyncio.run(exercise())


def test_background_refresh_updates_cache(monkeypatch):
    reset_scanner_cache()
    key = ("hyperliquid", "4h")

    async def fake_scan(*args, **kwargs):
        scanner._scan_cache[key] = (monotonic(), cached_payload("AVAXUSDT"))
        return scanner._scan_cache[key][1]

    async def exercise():
        monkeypatch.setattr(scanner, "run_scan", fake_scan)
        status = scanner.trigger_top_ideas_refresh("hyperliquid", "4h")
        assert status["started"] is True
        task = scanner._refresh_tasks[key]
        await asyncio.wait_for(task, timeout=1)
        result = await scanner.cached_top_ideas("hyperliquid", "4h")
        assert result["ideas"][0].symbol == "AVAXUSDT"
        assert result["refreshing"] is False
        assert result["last_refresh_finished_at"] is not None

    asyncio.run(exercise())


def test_duplicate_refresh_does_not_start_twice(monkeypatch):
    reset_scanner_cache()
    key = ("hyperliquid", "4h")
    calls = 0
    release = asyncio.Event()

    async def slow_scan(*args, **kwargs):
        nonlocal calls
        calls += 1
        await release.wait()
        scanner._scan_cache[key] = (monotonic(), cached_payload())
        return scanner._scan_cache[key][1]

    async def exercise():
        monkeypatch.setattr(scanner, "run_scan", slow_scan)
        first = scanner.trigger_top_ideas_refresh("hyperliquid", "4h")
        second = scanner.trigger_top_ideas_refresh("hyperliquid", "4h")
        assert first["started"] is True
        assert second["started"] is False
        assert calls == 0
        await asyncio.sleep(0)
        assert calls == 1
        task = scanner._refresh_tasks[key]
        release.set()
        await asyncio.wait_for(task, timeout=1)

    asyncio.run(exercise())
