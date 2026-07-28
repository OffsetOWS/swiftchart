from __future__ import annotations

from fastapi.testclient import TestClient

from app.config import get_settings
from app.ea.service import EAExecutionService
from app.main import app
from app.mt5.models import ForexAutoSignal
from app.utils import database


class FakeSession:
    market_open = True


def configure_db(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'swiftchart.db'}")
    monkeypatch.setenv("EA_API_KEY", "test-ea-key")
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
        "lot_size": 0.3,
        "trade_id": "ea-test-1",
    }
    payload.update(overrides)
    return ForexAutoSignal(**payload)


def headers():
    return {"X-SwiftChart-EA-Key": "test-ea-key"}


def test_signal_is_validated_and_queued_for_ea(monkeypatch, tmp_path):
    configure_db(monkeypatch, tmp_path)
    monkeypatch.setattr("app.ea.service.forex_session_state", lambda: FakeSession())
    service = EAExecutionService()

    response = service.receive_signal(signal())

    assert response.accepted is True
    assert response.signal is not None
    assert response.signal.status.value == "received"
    assert response.signal.signal.lot_size == 0.3


def test_dry_run_validates_without_queueing(monkeypatch, tmp_path):
    configure_db(monkeypatch, tmp_path)
    monkeypatch.setattr("app.ea.service.forex_session_state", lambda: FakeSession())
    service = EAExecutionService()

    response = service.receive_signal(signal(), dry_run=True)
    pending = service.pending_signals().signals

    assert response.accepted is True
    assert response.dry_run is True
    assert pending == []


def test_ea_pending_signals_requires_api_key(monkeypatch, tmp_path):
    configure_db(monkeypatch, tmp_path)
    client = TestClient(app)

    response = client.get("/api/ea/pending-signals")

    assert response.status_code == 401


def test_ea_fetches_pending_signal_and_marks_executing(monkeypatch, tmp_path):
    configure_db(monkeypatch, tmp_path)
    monkeypatch.setattr("app.ea.service.forex_session_state", lambda: FakeSession())
    EAExecutionService().receive_signal(signal())
    client = TestClient(app)

    response = client.get("/api/ea/pending-signals", headers=headers())

    assert response.status_code == 200
    body = response.json()
    assert len(body["signals"]) == 1
    assert body["signals"][0]["trade_id"] == "ea-test-1"
    assert body["signals"][0]["status"] == "executing"


def test_ea_trade_update_records_execution_state(monkeypatch, tmp_path):
    configure_db(monkeypatch, tmp_path)
    monkeypatch.setattr("app.ea.service.forex_session_state", lambda: FakeSession())
    EAExecutionService().receive_signal(signal())
    client = TestClient(app)

    response = client.post(
        "/api/ea/trade-update",
        headers=headers(),
        json={
            "trade_id": "ea-test-1",
            "status": "executed",
            "broker_order_id": "12345",
            "executed_price": 1.0846,
            "executed_volume": 0.3,
        },
    )

    assert response.status_code == 200
    assert response.json()["accepted"] is True
    assert response.json()["signal"]["status"] == "executed"


def test_ea_heartbeat_and_config(monkeypatch, tmp_path):
    configure_db(monkeypatch, tmp_path)
    client = TestClient(app)

    heartbeat = client.post(
        "/api/ea/heartbeat",
        headers=headers(),
        json={"client_id": "terminal-1", "ea_version": "0.1.0", "trading_allowed": True},
    )
    config = client.get("/api/ea/config", headers=headers())

    assert heartbeat.status_code == 200
    assert heartbeat.json()["accepted"] is True
    assert config.status_code == 200
    assert config.json()["production_execution_path"] == "mql5_expert_advisor"
    assert config.json()["idle_poll_interval_seconds"] == 20
    assert config.json()["error_retry_interval_seconds"] == 12
    assert config.json()["active_poll_interval_seconds"] == 5
    assert "executed" in config.json()["trade_update_states"]
