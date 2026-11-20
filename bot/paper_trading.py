from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any

import httpx

from app.services.market_data import get_candles_cached

logger = logging.getLogger(__name__)

ACTIVE_STATUSES = ("open", "tp1_hit")
TERMINAL_STATUSES = ("tp2_hit", "sl_hit", "closed")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _number(value: Any) -> float:
    return float(value)


def supabase_config() -> tuple[str, str]:
    url = (os.getenv("SUPABASE_URL") or os.getenv("VITE_SUPABASE_URL") or "").rstrip("/")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
    return url, key


def supabase_enabled() -> bool:
    return all(supabase_config())


def _headers(key: str, *, prefer: str | None = None) -> dict[str, str]:
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    if prefer:
        headers["Prefer"] = prefer
    return headers


async def _request(method: str, path: str, **kwargs) -> httpx.Response:
    url, key = supabase_config()
    if not url or not key:
        raise RuntimeError("Telegram paper trading is not configured.")
    headers = {**_headers(key), **kwargs.pop("headers", {})}
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.request(method, f"{url}/rest/v1/{path}", headers=headers, **kwargs)
    response.raise_for_status()
    return response


async def create_paper_trade(telegram_user_id: int, signal: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    params = {
        "telegram_user_id": f"eq.{int(telegram_user_id)}",
        "signal_id": f"eq.{signal['signal_id']}",
        "select": "*",
        "limit": "1",
    }
    existing = (await _request("GET", "paper_trades", params=params)).json()
    if existing:
        return existing[0], True

    payload = {
        "telegram_user_id": int(telegram_user_id),
        "signal_id": signal["signal_id"],
        "pair": signal["pair"].upper(),
        "side": signal["side"].lower(),
        "entry": _number(signal["entry"]),
        "stop_loss": _number(signal["stop_loss"]),
        "tp1": _number(signal["tp1"]),
        "tp2": _number(signal["tp2"]),
        "exchange": signal.get("exchange", "hyperliquid").lower(),
        "timeframe": signal.get("timeframe", "4h").lower(),
        "status": "open",
        "opened_at": _now_iso(),
        "pnl_r": 0,
        "paper_trade": True,
        "source": "telegram",
    }
    try:
        rows = (
            await _request(
                "POST",
                "paper_trades",
                params={"select": "*"},
                json=payload,
                headers={"Prefer": "return=representation"},
            )
        ).json()
        return rows[0], False
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code != 409:
            raise
        rows = (await _request("GET", "paper_trades", params=params)).json()
        if not rows:
            raise
        return rows[0], True


async def list_paper_trades(telegram_user_id: int, limit: int = 20) -> list[dict[str, Any]]:
    response = await _request(
        "GET",
        "paper_trades",
        params={
            "telegram_user_id": f"eq.{int(telegram_user_id)}",
            "select": "id,signal_id,pair,side,entry,stop_loss,tp1,tp2,status,pnl_r,opened_at,closed_at",
            "order": "opened_at.desc",
            "limit": str(limit),
        },
    )
    return response.json()


async def list_open_paper_trades(telegram_user_id: int, limit: int = 20) -> list[dict[str, Any]]:
    response = await _request(
        "GET",
        "paper_trades",
        params={
            "telegram_user_id": f"eq.{int(telegram_user_id)}",
            "status": f"in.({','.join(ACTIVE_STATUSES)})",
            "select": "id,signal_id,pair,side,entry,stop_loss,tp1,tp2,status,pnl_r,opened_at,closed_at",
            "order": "opened_at.desc",
            "limit": str(limit),
        },
    )
    return response.json()


async def list_active_paper_trades(limit: int = 500) -> list[dict[str, Any]]:
    response = await _request(
        "GET",
        "paper_trades",
        params={
            "status": f"in.({','.join(ACTIVE_STATUSES)})",
            "telegram_user_id": "not.is.null",
            "select": "id,pair,side,entry,stop_loss,tp1,tp2,status,opened_at,exchange,timeframe",
            "order": "opened_at.asc",
            "limit": str(limit),
        },
    )
    return response.json()


async def update_paper_trade(trade_id: str, changes: dict[str, Any]) -> None:
    await _request(
        "PATCH",
        "paper_trades",
        params={"id": f"eq.{trade_id}"},
        json=changes,
        headers={"Prefer": "return=minimal"},
    )


def evaluate_trade(trade: dict[str, Any], high: float, low: float) -> dict[str, Any] | None:
    side = str(trade["side"]).lower()
    status = str(trade["status"]).lower()
    entry = _number(trade["entry"])
    stop = _number(trade["stop_loss"])
    tp1 = _number(trade["tp1"])
    tp2 = _number(trade["tp2"])
    risk = abs(entry - stop)
    if risk <= 0:
        return None

    if side == "long":
        stop_hit = low <= stop
        tp1_hit = high >= tp1
        tp2_hit = high >= tp2
        tp1_r = (tp1 - entry) / risk
        tp2_r = (tp2 - entry) / risk
    else:
        stop_hit = high >= stop
        tp1_hit = low <= tp1
        tp2_hit = low <= tp2
        tp1_r = (entry - tp1) / risk
        tp2_r = (entry - tp2) / risk

    # Without tick data, assume the adverse level was reached first when both
    # stop and target fall inside the same candle.
    if stop_hit:
        return {"status": "sl_hit", "pnl_r": -1.0, "closed_at": _now_iso()}
    if tp2_hit:
        return {"status": "tp2_hit", "pnl_r": round(tp2_r, 4), "closed_at": _now_iso()}
    if status == "open" and tp1_hit:
        return {"status": "tp1_hit", "pnl_r": round(tp1_r, 4)}
    return None


async def check_paper_trades_once() -> dict[str, int]:
    if not supabase_enabled():
        return {"checked": 0, "updated": 0}
    trades = await list_active_paper_trades()
    checked = 0
    updated = 0
    for trade in trades:
        checked += 1
        try:
            candles = await get_candles_cached(
                trade.get("exchange") or "hyperliquid",
                trade["pair"],
                trade.get("timeframe") or "1h",
                3,
            )
            if candles.empty:
                continue
            opened_at = datetime.fromisoformat(str(trade["opened_at"]).replace("Z", "+00:00"))
            eligible = candles[candles["timestamp"] >= opened_at]
            for _, candle in eligible.iterrows():
                changes = evaluate_trade(trade, high=float(candle["high"]), low=float(candle["low"]))
                if not changes:
                    continue
                await update_paper_trade(str(trade["id"]), changes)
                trade.update(changes)
                updated += 1
                if changes["status"] in TERMINAL_STATUSES:
                    break
        except Exception:
            logger.exception("Paper trade check failed trade_id=%s pair=%s", trade.get("id"), trade.get("pair"))
    return {"checked": checked, "updated": updated}


async def paper_trade_worker() -> None:
    interval = max(30, int(os.getenv("PAPER_TRADE_CHECK_INTERVAL_SECONDS", "60")))
    startup_delay = max(0, int(os.getenv("PAPER_TRADE_STARTUP_DELAY_SECONDS", "10")))
    await asyncio.sleep(startup_delay)
    while True:
        try:
            result = await check_paper_trades_once()
            if result["checked"]:
                logger.info("Paper trade check complete: %s", result)
        except Exception:
            logger.exception("Paper trade worker failed")
        await asyncio.sleep(interval)
