from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from app.utils.auth import verify_supabase_jwt


def b64url(payload: bytes) -> str:
    return base64.urlsafe_b64encode(payload).decode("utf-8").rstrip("=")


def token_for(user_id: str, secret: str) -> str:
    header = b64url(json.dumps({"alg": "HS256", "typ": "JWT"}).encode("utf-8"))
    body = b64url(json.dumps({"sub": user_id, "exp": int(time.time()) + 3600, "aud": "authenticated"}).encode("utf-8"))
    signature = b64url(hmac.new(secret.encode("utf-8"), f"{header}.{body}".encode("utf-8"), hashlib.sha256).digest())
    return f"{header}.{body}.{signature}"


def test_paper_trade_requires_auth(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'swiftchart.db'}")
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "test-secret")
    get_settings.cache_clear()
    import app.utils.database as database

    database._INITIALIZED = False
    client = TestClient(app)

    response = client.get("/api/paper-trades")

    assert response.status_code == 401


def test_paper_trade_create_is_user_scoped_and_deduped(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'swiftchart.db'}")
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "test-secret")
    get_settings.cache_clear()
    import app.utils.database as database

    database._INITIALIZED = False
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {token_for('user-123', 'test-secret')}"}
    payload = {
        "signal_id": "signal-btc-long-1",
        "symbol": "BTCUSDT",
        "timeframe": "4h",
        "exchange": "hyperliquid",
        "direction": "Long",
        "entry_price": 100,
        "stop_loss": 95,
        "take_profit_1": 110,
        "take_profit_2": 120,
        "size": 0,
        "risk_reward": 2.5,
        "setup_score": 82,
        "confidence": 82,
    }

    first = client.post("/api/paper-trade", json=payload, headers=headers)
    second = client.post("/api/paper-trade", json=payload, headers=headers)
    listed = client.get("/api/paper-trades", headers=headers)

    assert first.status_code == 200
    assert first.json()["status"] == "taken"
    assert first.json()["user_id"] == "user-123"
    assert second.status_code == 200
    assert second.json()["already_taken"] is True
    assert len(listed.json()) == 1


def test_supabase_auth_api_fallback(monkeypatch):
    monkeypatch.delenv("SUPABASE_JWT_SECRET", raising=False)
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon-key")
    get_settings.cache_clear()

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"id": "user-456", "email": "user@example.com"}

    class FakeClient:
        def __init__(self, timeout):
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get(self, url, headers):
            assert url == "https://example.supabase.co/auth/v1/user"
            assert headers["apikey"] == "anon-key"
            assert headers["Authorization"] == "Bearer access-token"
            return FakeResponse()

    monkeypatch.setattr("app.utils.auth.httpx.Client", FakeClient)

    user = verify_supabase_jwt("access-token")

    assert user.id == "user-456"
    assert user.email == "user@example.com"


def test_supabase_auth_api_fallback_uses_token_issuer(monkeypatch):
    monkeypatch.delenv("SUPABASE_JWT_SECRET", raising=False)
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_ANON_KEY", raising=False)
    get_settings.cache_clear()
    token = ".".join(
        [
            b64url(json.dumps({"alg": "HS256", "typ": "JWT"}).encode("utf-8")),
            b64url(json.dumps({"iss": "https://project.supabase.co/auth/v1"}).encode("utf-8")),
            "signature",
        ]
    )

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"id": "user-789"}

    class FakeClient:
        def __init__(self, timeout):
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get(self, url, headers):
            assert url == "https://project.supabase.co/auth/v1/user"
            assert "apikey" not in headers
            assert headers["Authorization"] == f"Bearer {token}"
            return FakeResponse()

    monkeypatch.setattr("app.utils.auth.httpx.Client", FakeClient)

    user = verify_supabase_jwt(token)

    assert user.id == "user-789"
