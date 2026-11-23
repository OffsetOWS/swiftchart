import importlib.util
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "check_staged_security",
    Path(__file__).resolve().parents[2] / "scripts" / "check_staged_security.py",
)
checker = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(checker)


# Built at runtime so secret scanners never see a token-shaped literal.
FAKE_TELEGRAM_TOKEN = "123456789" + ":" + "A" * 35


def errors(text: str) -> list[str]:
    return checker.check_env_template(".env.example", text)


def test_harmless_defaults_pass():
    text = "\n".join(
        [
            "# comment",
            "",
            "APP_NAME=SwiftChart",
            "ENVIRONMENT=production",
            "DATABASE_URL=sqlite:///./swiftchart.db",
            "HYPERLIQUID_BASE_URL=https://api.hyperliquid.xyz",
            "FRONTEND_ORIGINS=http://localhost:5173,https://swiftchart.app",
            "LIVE_TRADING_ENABLED=false",
            "DEFAULT_TIMEFRAME=4h",
            "DEFAULT_RISK_PER_TRADE=1",
            "EXECUTION_SYMBOL_ALLOWLIST=BTC,ETH,SOL",
            "BOT_STATE_PATH=/opt/swiftchart/.swiftchart_bot_state.json",
            "API_BIND=127.0.0.1",
        ]
    )
    assert errors(text) == []


@pytest.mark.parametrize(
    "line",
    [
        "TELEGRAM_BOT_TOKEN=",
        "SUPABASE_JWT_SECRET=",
        "HYPERLIQUID_PRIVATE_KEY=YOUR_HYPERLIQUID_PRIVATE_KEY",
        "XAI_API_KEY=YOUR_XAI_API_KEY",
        'INTERNAL_API_SECRET=""',
    ],
)
def test_empty_or_placeholder_credentials_pass(line):
    assert errors(line) == []


@pytest.mark.parametrize(
    "line",
    [
        "TELEGRAM_BOT_TOKEN=" + FAKE_TELEGRAM_TOKEN,
        "SUPABASE_JWT_SECRET=super-secret-value",
        "HYPERLIQUID_PRIVATE_KEY=0x" + "ab12" * 16,
        "BINANCE_API_KEY=abc",
        "VITE_SUPABASE_ANON_KEY=eyJhbGciOi.payload.sig",
        "TELEGRAM_ADMIN_ID=123456789",
        "TRADING_AGENT_ALLOWED_USER_IDS=123,456",
        "HYPERLIQUID_WALLET_ADDRESS=0x1234567890abcdef1234567890abcdef12345678",
    ],
)
def test_credential_keys_with_values_are_blocked(line):
    assert errors(line)


@pytest.mark.parametrize(
    "line",
    [
        "DATABASE_URL=postgresql://admin:hunter2@db.example.com:5432/app",
        "VITE_API_BASE=http://203.0.113.10:8000",
        "SOME_SETTING=" + FAKE_TELEGRAM_TOKEN,
        "SOME_SETTING=Zx9Qm2Lk8Vb7Nc6Xa5Sd4Fg3Hj2Kl1Pq0Wo9",
    ],
)
def test_secret_shaped_values_are_blocked_under_any_key(line):
    assert errors(line)


def test_malformed_line_is_reported():
    assert errors("not an assignment")


def test_private_paths_are_blocked():
    assert checker.check_path(".env")
    assert checker.check_path("backend/swiftchart.db")
    assert checker.check_path("logs/app.log")
    assert checker.check_path("certs/server.pem")
    assert checker.check_path(".env.example") is None
    assert checker.check_path("backend/app/main.py") is None


def test_repository_env_templates_pass():
    root = Path(__file__).resolve().parents[2]
    for template in root.rglob(".env.example"):
        if "node_modules" in template.parts:
            continue
        rel = template.relative_to(root).as_posix()
        assert checker.check_env_template(rel, template.read_text()) == [], rel
