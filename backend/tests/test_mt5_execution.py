from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.config import get_settings
from app.mt5.bridge import MT5Bridge
from app.mt5.models import (
    CloseTradeRequest,
    ForexAutoSignal,
    MT5AccountSnapshot,
    MT5ConnectRequest,
    MT5OrderResult,
    SymbolSnapshot,
)
from app.mt5.service import ForexExecutionService
from app.utils import database


@dataclass
class FakeSession:
    market_open: bool = True


class FakeMT5Bridge(MT5Bridge):
    def __init__(self, *, spread_pips: float = 1.0, trade_allowed: bool = True) -> None:
        self.spread_pips = spread_pips
        self.trade_allowed = trade_allowed

    def connect(self, request: MT5ConnectRequest) -> MT5AccountSnapshot:
        return self.account()

    def account(self) -> MT5AccountSnapshot:
        return MT5AccountSnapshot(
            login=123,
            server="Demo",
            currency="USD",
            balance=10_000,
            equity=10_000,
            margin_free=9_000,
            leverage=100,
            trade_allowed=self.trade_allowed,
            connected=True,
        )

    def symbol(self, pair: str) -> SymbolSnapshot:
        return SymbolSnapshot(
            symbol=pair,
            bid=1.0845,
            ask=1.0846,
            point=0.00001,
            digits=5,
            spread_pips=self.spread_pips,
            trade_allowed=True,
            volume_min=0.01,
            volume_max=100,
            volume_step=0.01,
            contract_size=100_000,
            pip_size=0.0001,
            pip_value_per_lot=10,
        )

    def open_market_order(self, signal: ForexAutoSignal, lot_size: float) -> MT5OrderResult:
        return MT5OrderResult(success=True, order_id=42, position_id=420, executed_price=signal.entry, volume=lot_size)

    def close_position(self, request: CloseTradeRequest, position_id: int | None = None) -> MT5OrderResult:
        return MT5OrderResult(success=True, order_id=99, position_id=position_id, volume=request.volume)

    def modify_stop_loss(self, position_id: int, stop_loss: float, take_profit: float | None = None) -> MT5OrderResult:
        return MT5OrderResult(success=True, position_id=position_id)

    def positions(self) -> list[dict]:
        return []


def configure_db(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'swiftchart.db'}")
    get_settings.cache_clear()
    database._INITIALIZED = False
    database.init_db()


def signal(**overrides) -> ForexAutoSignal:
    payload = {
        "pair": "EURUSD",
        "side": "BUY",
        "timeframe": "H1",
        "entry": 1.08452,
        "stop_loss": 1.08120,
        "tp1": 1.08810,
        "tp2": 1.09150,
        "confidence": 84,
        "setup_score": 88,
        "risk_percent": 1,
        "trade_id": "fx-test-1",
    }
    payload.update(overrides)
    return ForexAutoSignal(**payload)


def test_mt5_signal_validation_calculates_dynamic_lot_size(monkeypatch, tmp_path):
    configure_db(monkeypatch, tmp_path)
    monkeypatch.setattr("app.mt5.service.forex_session_state", lambda: FakeSession(True))
    service = ForexExecutionService(FakeMT5Bridge())

    result = service.validate_signal(signal())

    assert result.accepted is True
    assert result.sizing is not None
    assert result.sizing.stop_loss_pips == pytest.approx(33.2)
    assert result.sizing.lot_size == 0.3
    assert result.sizing.risk_amount == 100


def test_mt5_signal_validation_rejects_low_confidence(monkeypatch, tmp_path):
    configure_db(monkeypatch, tmp_path)
    monkeypatch.setattr("app.mt5.service.forex_session_state", lambda: FakeSession(True))
    service = ForexExecutionService(FakeMT5Bridge())

    result = service.validate_signal(signal(confidence=70))

    assert result.accepted is False
    assert any("below minimum" in reason for reason in result.reasons)


def test_mt5_open_trade_persists_successful_order(monkeypatch, tmp_path):
    configure_db(monkeypatch, tmp_path)
    monkeypatch.setattr("app.mt5.service.forex_session_state", lambda: FakeSession(True))
    service = ForexExecutionService(FakeMT5Bridge())

    response = service.open_trade(signal())

    assert response.accepted is True
    assert response.trade is not None
    assert response.trade.status.value == "OPEN"
    assert response.trade.mt5_order_id == 42


def test_mt5_validation_rejects_duplicate_trade(monkeypatch, tmp_path):
    configure_db(monkeypatch, tmp_path)
    monkeypatch.setattr("app.mt5.service.forex_session_state", lambda: FakeSession(True))
    service = ForexExecutionService(FakeMT5Bridge())

    assert service.open_trade(signal()).accepted is True
    result = service.validate_signal(signal())

    assert result.accepted is False
    assert "Duplicate trade ID." in result.reasons
