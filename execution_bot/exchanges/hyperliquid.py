from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
from typing import Any

import httpx
from eth_account import Account
from hyperliquid.exchange import Exchange
from hyperliquid.info import Info
from hyperliquid.utils.types import Cloid

from execution_bot.config import get_execution_settings
from execution_bot.exchanges.base import ExecutionExchange
from execution_bot.models import Candle, ExecutionPlan, MarketSnapshot


TIMEFRAME_TO_HL = {
    "1m": "1m",
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "1h": "1h",
    "2h": "1h",
    "4h": "4h",
    "1d": "1d",
}


class HyperliquidExecutionExchange(ExecutionExchange):
    name = "hyperliquid"

    def __init__(self) -> None:
        self.settings = get_execution_settings()
        self.base_url = self.settings.hyperliquid_base_url.rstrip("/")

    async def _post_info(self, payload: dict) -> dict | list:
        async def request() -> dict | list:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.post(f"{self.base_url}/info", json=payload)
                response.raise_for_status()
                return response.json()

        return await self._retry_async(request)

    async def _retry_async(self, operation, attempts: int = 3):
        last_error: Exception | None = None
        for index in range(attempts):
            try:
                return await operation()
            except Exception as exc:
                last_error = exc
                if index == attempts - 1:
                    break
                await asyncio.sleep(1 + index)
        raise RuntimeError(f"Hyperliquid connection failed after {attempts} attempts: {last_error}") from last_error

    async def _retry_thread(self, operation, attempts: int = 3):
        async def call():
            return await asyncio.to_thread(operation)

        return await self._retry_async(call, attempts=attempts)

    async def get_market_snapshot(self, symbol: str, timeframe: str, limit: int = 120) -> MarketSnapshot:
        coin = symbol.upper().replace("USDT", "")
        interval = TIMEFRAME_TO_HL.get(timeframe.lower(), "15m")
        interval_minutes = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240, "1d": 1440}[interval]
        now_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
        start_ms = now_ms - limit * interval_minutes * 60 * 1000
        rows = await self._post_info({"type": "candleSnapshot", "req": {"coin": coin, "interval": interval, "startTime": start_ms, "endTime": now_ms}})
        candles = [
            Candle(
                timestamp=datetime.fromtimestamp(row["t"] / 1000, tz=timezone.utc),
                open=float(row["o"]),
                high=float(row["h"]),
                low=float(row["l"]),
                close=float(row["c"]),
                volume=float(row.get("v", 0)),
            )
            for row in rows
        ]
        bid = ask = None
        mark_price = None
        try:
            mids = await self._post_info({"type": "allMids"})
            if isinstance(mids, dict) and coin in mids:
                mid = float(mids[coin])
                mark_price = mid
                bid = mid * 0.9995
                ask = mid * 1.0005
        except httpx.HTTPError:
            pass
        return MarketSnapshot(candles=candles, bid=bid, ask=ask, mark_price=mark_price, perp_volume_24h=await self._perp_volume_24h(coin))

    async def _perp_volume_24h(self, coin: str) -> float | None:
        try:
            data = await self._post_info({"type": "metaAndAssetCtxs"})
        except Exception:
            return None
        if not isinstance(data, list) or len(data) < 2:
            return None
        universe = data[0].get("universe", []) if isinstance(data[0], dict) else []
        contexts = data[1] if isinstance(data[1], list) else []
        for index, item in enumerate(universe):
            if str(item.get("name", "")).upper() != coin.upper():
                continue
            context = contexts[index] if index < len(contexts) and isinstance(contexts[index], dict) else {}
            try:
                return float(context.get("dayNtlVlm"))
            except (TypeError, ValueError):
                return None
        return None

    async def place_order(self, plan: ExecutionPlan) -> dict:
        if not self.settings.live_enabled:
            return {"id": f"paper-hl-{plan.symbol}", "status": "paper", "message": "Live mode is disabled."}
        return await self._run_live_order(plan)

    async def close_position(self, symbol: str, size: float) -> dict:
        if not self.settings.live_enabled:
            return {"id": f"paper-hl-close-{symbol}", "status": "paper", "size": size}
        coin = symbol.upper().replace("USDT", "")
        exchange = self._signed_exchange()
        result = await self._retry_thread(lambda: exchange.market_close(coin, sz=size, slippage=0.01))
        return {"id": f"hl-close-{coin}-{int(datetime.now(timezone.utc).timestamp())}", "status": "submitted", "result": result}

    def _signed_exchange(self) -> Exchange:
        secret = self.settings.effective_hyperliquid_signing_secret
        if not secret:
            raise ValueError(
                "Hyperliquid live mode requires a 32-byte private key in HYPERLIQUID_API_SECRET "
                "or HYPERLIQUID_PRIVATE_KEY. The current HYPERLIQUID_API_KEY value is not a private key."
            )
        wallet = Account.from_key(secret)
        account_address = self.settings.effective_hyperliquid_wallet_address or None
        return Exchange(
            wallet,
            base_url=self.base_url,
            account_address=account_address,
            timeout=20,
        )

    async def _run_live_order(self, plan: ExecutionPlan) -> dict:
        coin = plan.symbol.upper().replace("USDT", "")
        exchange = self._signed_exchange()
        leverage = min(self.settings.max_leverage, max(1, int(round(plan.leverage))))
        leverage_result = await self._retry_thread(lambda: exchange.update_leverage(int(leverage), coin, is_cross=True))
        is_buy = plan.side.value == "BUY"
        size = self._round_size(exchange, coin, plan.position_size)
        if size <= 0:
            raise RuntimeError(f"Calculated {coin} order size is below Hyperliquid minimum precision.")
        signal_key = plan.signal.signal_id or f"{plan.signal.pair}:{plan.signal.side.value}:{plan.signal.entry}:{plan.signal.created_at.isoformat()}"
        order_result = await self._retry_thread(lambda: exchange.market_open(coin, is_buy, sz=size, slippage=0.01, cloid=self._cloid(signal_key, "entry")))
        fill = self._filled_status(order_result)
        if fill is None:
            raise RuntimeError(f"Hyperliquid entry order was not filled: {order_result}")

        fill_price = float(fill.get("avgPx") or fill.get("px") or plan.entry)
        entry_oid = fill.get("oid")
        stop_result = await self._retry_thread(lambda: exchange.order(
            coin,
            not is_buy,
            size,
            plan.stop_loss,
            order_type={"trigger": {"triggerPx": plan.stop_loss, "isMarket": True, "tpsl": "sl"}},
            reduce_only=True,
            cloid=self._cloid(signal_key, "stop"),
        ))
        stop_oid = self._resting_oid(stop_result)

        tp_results: list[dict[str, Any]] = []
        tp_oids: list[int | str] = []
        remaining = size
        for index, target in enumerate(plan.take_profits):
            if index == len(plan.take_profits) - 1:
                tp_size = remaining
            else:
                tp_size = self._round_size(exchange, coin, size * float(target["close_percent"]) / 100)
                remaining = self._round_size(exchange, coin, max(0, remaining - tp_size))
            if tp_size <= 0:
                continue
            target_price = float(target["target"])
            result = await self._retry_thread(lambda target_price=target_price, tp_size=tp_size: exchange.order(
                coin,
                not is_buy,
                tp_size,
                target_price,
                order_type={"trigger": {"triggerPx": target_price, "isMarket": True, "tpsl": "tp"}},
                reduce_only=True,
                cloid=self._cloid(signal_key, f"tp{index + 1}"),
            ))
            tp_results.append(result)
            oid = self._resting_oid(result)
            if oid is not None:
                tp_oids.append(oid)

        verified_orders = await self._verify_protection_orders(coin, stop_oid, tp_oids)
        filled_at = datetime.now(timezone.utc).isoformat()
        return {
            "id": str(entry_oid or f"hl-live-{coin}-{int(datetime.now(timezone.utc).timestamp())}"),
            "status": "filled" if verified_orders["all_protection_active"] else "protection_incomplete",
            "mode": "live",
            "coin": coin,
            "is_buy": is_buy,
            "size": size,
            "leverage": leverage,
            "fill_price": fill_price,
            "filled_at": filled_at,
            "stop_loss": plan.stop_loss,
            "take_profits": plan.take_profits,
            "stop_order_id": stop_oid,
            "tp_order_ids": tp_oids,
            "verification": {
                "order_placed": True,
                "fill_received": True,
                "stop_loss_active": verified_orders["stop_loss_active"],
                "tp_orders_active": verified_orders["tp_orders_active"],
                "all_protection_active": verified_orders["all_protection_active"],
            },
            "leverage_result": leverage_result,
            "order_result": order_result,
            "stop_result": stop_result,
            "tp_results": tp_results,
            "message": "Live order filled and exchange-native SL/TP trigger orders submitted.",
        }

    async def sync_account_balance(self) -> float | None:
        summary = await self.account_summary()
        balance = summary.get("balance")
        return float(balance) if balance is not None else None

    async def account_summary(self) -> dict:
        address = self._account_address()
        if not address:
            return {}

        def load_state():
            info = Info(self.base_url, skip_ws=True, timeout=20)
            return info.user_state(address)

        state = await self._retry_thread(load_state)
        margin = state.get("marginSummary") or state.get("crossMarginSummary") or {}
        positions = []
        for item in state.get("assetPositions", []):
            position = item.get("position", {})
            size = float(position.get("szi") or 0)
            if abs(size) > 0:
                positions.append(
                    {
                        "coin": position.get("coin"),
                        "size": size,
                        "entry": float(position.get("entryPx") or 0),
                        "unrealized_pnl": float(position.get("unrealizedPnl") or 0),
                    }
                )
        return {"balance": float(margin["accountValue"]) if margin.get("accountValue") is not None else None, "positions": positions}

    async def recent_fills(self, symbol: str, start_time_ms: int) -> list[dict]:
        address = self._account_address()
        if not address:
            return []
        coin = symbol.upper().replace("USDT", "")

        def load_fills():
            info = Info(self.base_url, skip_ws=True, timeout=20)
            return info.user_fills_by_time(address, start_time=start_time_ms)

        fills = await self._retry_thread(load_fills)
        return [fill for fill in fills if fill.get("coin") == coin]

    def _account_address(self) -> str | None:
        if self.settings.effective_hyperliquid_wallet_address:
            return self.settings.effective_hyperliquid_wallet_address
        secret = self.settings.effective_hyperliquid_signing_secret
        if not secret:
            return None
        return Account.from_key(secret).address

    async def _verify_protection_orders(self, coin: str, stop_oid: int | str | None, tp_oids: list[int | str]) -> dict[str, bool]:
        address = self._account_address()
        if not address:
            return {"stop_loss_active": False, "tp_orders_active": False, "all_protection_active": False}

        def load_orders():
            info = Info(self.base_url, skip_ws=True, timeout=20)
            return info.frontend_open_orders(address)

        open_orders = await self._retry_thread(load_orders)
        active_oids = {str(order.get("oid")) for order in open_orders if order.get("coin") == coin and order.get("reduceOnly")}
        stop_active = stop_oid is not None and str(stop_oid) in active_oids
        tp_active = bool(tp_oids) and all(str(oid) in active_oids for oid in tp_oids)
        return {"stop_loss_active": stop_active, "tp_orders_active": tp_active, "all_protection_active": stop_active and tp_active}

    def _filled_status(self, result: dict) -> dict | None:
        for status in self._statuses(result):
            fill = status.get("filled") if isinstance(status, dict) else None
            if fill:
                return fill
            error = status.get("error") if isinstance(status, dict) else None
            if error:
                raise RuntimeError(f"Hyperliquid order rejected: {error}")
        return None

    def _resting_oid(self, result: dict) -> int | str | None:
        for status in self._statuses(result):
            if not isinstance(status, dict):
                continue
            resting = status.get("resting")
            if resting and resting.get("oid") is not None:
                return resting["oid"]
            filled = status.get("filled")
            if filled and filled.get("oid") is not None:
                return filled["oid"]
            error = status.get("error")
            if error:
                raise RuntimeError(f"Hyperliquid protective order rejected: {error}")
        return None

    def _statuses(self, result: dict) -> list[dict]:
        response = result.get("response") if isinstance(result, dict) else None
        data = response.get("data") if isinstance(response, dict) else None
        statuses = data.get("statuses") if isinstance(data, dict) else None
        return statuses if isinstance(statuses, list) else []

    def _cloid(self, signal_key: str, label: str) -> Cloid:
        digest = hashlib.sha256(f"swiftchart:{signal_key}:{label}".encode("utf-8")).digest()[:16]
        return Cloid.from_int(int.from_bytes(digest, "big"))

    def _round_size(self, exchange: Exchange, coin: str, size: float) -> float:
        asset = exchange.info.name_to_asset(coin)
        decimals = int(exchange.info.asset_to_sz_decimals[asset])
        quant = Decimal("1") if decimals <= 0 else Decimal("1").scaleb(-decimals)
        rounded = Decimal(str(size)).quantize(quant, rounding=ROUND_DOWN)
        return float(rounded)
