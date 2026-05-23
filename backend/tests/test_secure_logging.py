from __future__ import annotations

from app.utils.secure_logging import redact_sensitive


def test_backend_secure_logging_redacts_sensitive_query_and_auth_values():
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
