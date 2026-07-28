from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

from app.mt5.models import (
    CloseTradeRequest,
    ForexAutoSignal,
    ForexTradeSide,
    MT5AccountSnapshot,
    MT5ConnectRequest,
    MT5OrderResult,
    SymbolSnapshot,
)
from app.mt5.risk import pip_size_for_pair

logger = logging.getLogger(__name__)


class MT5BridgeError(RuntimeError):
    pass


class MT5Bridge(ABC):
    @abstractmethod
    def connect(self, request: MT5ConnectRequest) -> MT5AccountSnapshot:
        raise NotImplementedError

    @abstractmethod
    def account(self) -> MT5AccountSnapshot:
        raise NotImplementedError

    @abstractmethod
    def symbol(self, pair: str) -> SymbolSnapshot:
        raise NotImplementedError

    @abstractmethod
    def open_market_order(self, signal: ForexAutoSignal, lot_size: float) -> MT5OrderResult:
        raise NotImplementedError

    @abstractmethod
    def close_position(self, request: CloseTradeRequest, position_id: int | None = None) -> MT5OrderResult:
        raise NotImplementedError

    @abstractmethod
    def modify_stop_loss(self, position_id: int, stop_loss: float, take_profit: float | None = None) -> MT5OrderResult:
        raise NotImplementedError

    @abstractmethod
    def positions(self) -> list[dict[str, Any]]:
        raise NotImplementedError


class RealMT5Bridge(MT5Bridge):
    def __init__(self) -> None:
        try:
            import MetaTrader5 as mt5  # type: ignore
        except ImportError as exc:
            raise MT5BridgeError("MetaTrader5 package is not installed in this environment.") from exc
        self.mt5 = mt5

    def connect(self, request: MT5ConnectRequest) -> MT5AccountSnapshot:
        initialized = self.mt5.initialize(path=request.terminal_path) if request.terminal_path else self.mt5.initialize()
        if not initialized:
            raise MT5BridgeError(f"MT5 initialize failed: {self.mt5.last_error()}")
        if not self.mt5.login(request.login, password=request.password, server=request.server):
            raise MT5BridgeError(f"MT5 login failed: {self.mt5.last_error()}")
        return self.account()

    def account(self) -> MT5AccountSnapshot:
        info = self.mt5.account_info()
        if info is None:
            raise MT5BridgeError("MT5 account is not connected.")
        data = info._asdict()
        return MT5AccountSnapshot(
            login=data.get("login"),
            server=data.get("server"),
            currency=data.get("currency") or "USD",
            balance=float(data.get("balance") or 0),
            equity=float(data.get("equity") or 0),
            margin_free=float(data.get("margin_free") or 0),
            leverage=data.get("leverage"),
            trade_allowed=bool(data.get("trade_allowed")),
            connected=True,
            name=data.get("name"),
            company=data.get("company"),
        )

    def symbol(self, pair: str) -> SymbolSnapshot:
        symbol_name = pair.upper().replace("/", "")
        info = self.mt5.symbol_info(symbol_name)
        if info is None:
            raise MT5BridgeError(f"MT5 symbol {symbol_name} is not available.")
        if not info.visible and not self.mt5.symbol_select(symbol_name, True):
            raise MT5BridgeError(f"MT5 symbol {symbol_name} could not be selected.")
        tick = self.mt5.symbol_info_tick(symbol_name)
        if tick is None:
            raise MT5BridgeError(f"MT5 tick data unavailable for {symbol_name}.")
        pip_size = pip_size_for_pair(symbol_name)
        spread_pips = abs(float(tick.ask) - float(tick.bid)) / pip_size
        point = float(getattr(info, "point", 0) or pip_size / 10)
        contract_size = float(getattr(info, "trade_contract_size", 100_000) or 100_000)
        pip_value = _pip_value_per_lot(info, pip_size, point)
        return SymbolSnapshot(
            symbol=symbol_name,
            bid=float(tick.bid),
            ask=float(tick.ask),
            point=point,
            digits=int(getattr(info, "digits", 5) or 5),
            spread_pips=spread_pips,
            trade_allowed=bool(getattr(info, "trade_mode", 0)),
            volume_min=float(getattr(info, "volume_min", 0.01) or 0.01),
            volume_max=float(getattr(info, "volume_max", 100.0) or 100.0),
            volume_step=float(getattr(info, "volume_step", 0.01) or 0.01),
            contract_size=contract_size,
            pip_size=pip_size,
            pip_value_per_lot=pip_value,
        )

    def open_market_order(self, signal: ForexAutoSignal, lot_size: float) -> MT5OrderResult:
        symbol = self.symbol(signal.pair)
        order_type = self.mt5.ORDER_TYPE_BUY if signal.side == ForexTradeSide.buy else self.mt5.ORDER_TYPE_SELL
        price = symbol.ask if signal.side == ForexTradeSide.buy else symbol.bid
        request = {
            "action": self.mt5.TRADE_ACTION_DEAL,
            "symbol": symbol.symbol,
            "volume": lot_size,
            "type": order_type,
            "price": price,
            "sl": signal.stop_loss,
            "tp": signal.tp1,
            "deviation": 20,
            "magic": 860145,
            "comment": f"SwiftChart {signal.trade_id}"[:31],
            "type_time": self.mt5.ORDER_TIME_GTC,
            "type_filling": self.mt5.ORDER_FILLING_IOC,
        }
        result = self.mt5.order_send(request)
        return _order_result(result)

    def close_position(self, request: CloseTradeRequest, position_id: int | None = None) -> MT5OrderResult:
        if position_id is None:
            raise MT5BridgeError("position_id is required to close an MT5 position.")
        positions = self.mt5.positions_get(ticket=position_id)
        if not positions:
            raise MT5BridgeError(f"MT5 position {position_id} was not found.")
        position = positions[0]
        tick = self.mt5.symbol_info_tick(position.symbol)
        order_type = self.mt5.ORDER_TYPE_SELL if position.type == self.mt5.POSITION_TYPE_BUY else self.mt5.ORDER_TYPE_BUY
        price = tick.bid if order_type == self.mt5.ORDER_TYPE_SELL else tick.ask
        close_request = {
            "action": self.mt5.TRADE_ACTION_DEAL,
            "position": position.ticket,
            "symbol": position.symbol,
            "volume": request.volume or position.volume,
            "type": order_type,
            "price": price,
            "deviation": 20,
            "magic": 860145,
            "comment": f"SwiftChart close {request.trade_id}"[:31],
            "type_time": self.mt5.ORDER_TIME_GTC,
            "type_filling": self.mt5.ORDER_FILLING_IOC,
        }
        return _order_result(self.mt5.order_send(close_request))

    def modify_stop_loss(self, position_id: int, stop_loss: float, take_profit: float | None = None) -> MT5OrderResult:
        positions = self.mt5.positions_get(ticket=position_id)
        if not positions:
            raise MT5BridgeError(f"MT5 position {position_id} was not found.")
        position = positions[0]
        result = self.mt5.order_send(
            {
                "action": self.mt5.TRADE_ACTION_SLTP,
                "position": position_id,
                "symbol": position.symbol,
                "sl": stop_loss,
                "tp": take_profit if take_profit is not None else position.tp,
                "magic": 860145,
                "comment": "SwiftChart manage",
            }
        )
        return _order_result(result)

    def positions(self) -> list[dict[str, Any]]:
        positions = self.mt5.positions_get() or []
        return [position._asdict() for position in positions]


class UnavailableMT5Bridge(MT5Bridge):
    def _raise(self) -> None:
        raise MT5BridgeError("MT5 is unavailable. Install MetaTrader5 and run this service beside an MT5 terminal.")

    def connect(self, request: MT5ConnectRequest) -> MT5AccountSnapshot:
        self._raise()

    def account(self) -> MT5AccountSnapshot:
        self._raise()

    def symbol(self, pair: str) -> SymbolSnapshot:
        self._raise()

    def open_market_order(self, signal: ForexAutoSignal, lot_size: float) -> MT5OrderResult:
        self._raise()

    def close_position(self, request: CloseTradeRequest, position_id: int | None = None) -> MT5OrderResult:
        self._raise()

    def modify_stop_loss(self, position_id: int, stop_loss: float, take_profit: float | None = None) -> MT5OrderResult:
        self._raise()

    def positions(self) -> list[dict[str, Any]]:
        self._raise()


def get_mt5_bridge() -> MT5Bridge:
    try:
        return RealMT5Bridge()
    except MT5BridgeError as exc:
        logger.warning("MT5 bridge unavailable: %s", exc)
        return UnavailableMT5Bridge()


def _pip_value_per_lot(info: Any, pip_size: float, point: float) -> float:
    tick_value = float(getattr(info, "trade_tick_value", 0) or 0)
    tick_size = float(getattr(info, "trade_tick_size", 0) or point or 0)
    if tick_value > 0 and tick_size > 0:
        return tick_value * (pip_size / tick_size)
    contract_size = float(getattr(info, "trade_contract_size", 100_000) or 100_000)
    return contract_size * pip_size


def _order_result(result: Any) -> MT5OrderResult:
    if result is None:
        return MT5OrderResult(success=False, message="MT5 order_send returned no result.")
    data = result._asdict() if hasattr(result, "_asdict") else {}
    retcode = data.get("retcode")
    success = retcode in {10008, 10009}
    return MT5OrderResult(
        success=success,
        order_id=data.get("order"),
        position_id=data.get("deal") or data.get("order"),
        executed_price=data.get("price"),
        volume=data.get("volume"),
        retcode=retcode,
        message=data.get("comment") or ("Order accepted." if success else "Order rejected by MT5."),
    )

