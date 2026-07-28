from app.config import get_settings
from app.main import startup


def test_startup_does_not_start_crypto_scanner_by_default(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'swiftchart.db'}")
    monkeypatch.setenv("CRYPTO_BACKGROUND_SCANNER_ENABLED", "false")
    get_settings.cache_clear()

    called = False

    def fake_start_background_scanner():
        nonlocal called
        called = True

    monkeypatch.setattr("app.main.start_background_scanner", fake_start_background_scanner)
    __import__("asyncio").run(startup())

    assert called is False


def test_startup_can_start_crypto_scanner_when_enabled(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'swiftchart.db'}")
    monkeypatch.setenv("CRYPTO_BACKGROUND_SCANNER_ENABLED", "true")
    get_settings.cache_clear()

    called = False

    def fake_start_background_scanner():
        nonlocal called
        called = True

    monkeypatch.setattr("app.main.start_background_scanner", fake_start_background_scanner)
    __import__("asyncio").run(startup())

    assert called is True

