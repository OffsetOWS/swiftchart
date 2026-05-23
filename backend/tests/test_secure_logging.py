from __future__ import annotations

from app.utils.secure_logging import redact_sensitive
from app.utils.secure_logging import RedactingFilter


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


def test_backend_secure_logging_preserves_uvicorn_access_args():
    record = __import__("logging").LogRecord(
        "uvicorn.access",
        20,
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
