from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
import asyncio
import sqlite3
import pytest

from app.services.tracking_delivery import ensure_delivery_receipts, record_delivery
from app.services.tracking_ledger import first_crypto_delivery


def test_receipts_lookup_uses_earliest_success_across_old_and_new_tables():
    c = sqlite3.connect(":memory:"); c.row_factory = sqlite3.Row
    ensure_delivery_receipts(c)
    c.executemany("INSERT INTO crypto_delivery_receipts VALUES (?,?,?,?)", [
        (7,"1",1,"2026-09-01T12:00:00+00:00"), (7,"2",2,"2026-09-01T12:01:00+01:00")])
    assert first_crypto_delivery(c, 7) == "2026-09-01T12:01:00+01:00"
    assert first_crypto_delivery(c, 8) is None


def configure_db(monkeypatch, tmp_path):
    from app.utils import database
    from app.services import alert_dedupe
    monkeypatch.setattr(database, "get_settings", lambda: SimpleNamespace(database_url="sqlite:///" + str(tmp_path/"test.db")))
    monkeypatch.setattr(database, "_INITIALIZED", False)
    monkeypatch.setenv("ALERT_DEDUPE_STATE_PATH", str(tmp_path/"dedupe.json"))
    return database


def test_receipt_is_idempotent_and_cannot_be_reassigned(monkeypatch, tmp_path):
    from app.services.trade_history import save_trade_ideas
    from test_telegram_independent_alerts import idea
    db = configure_db(monkeypatch, tmp_path)
    trade_id = save_trade_ideas([idea()])[0]
    receipt = SimpleNamespace(message_id=10, date=datetime.now(UTC)-timedelta(seconds=1))
    assert record_delivery(trade_id, 1, receipt)
    assert record_delivery(trade_id, 1, receipt)
    with pytest.raises(ValueError): record_delivery(trade_id+1, 1, receipt)
    with db.get_connection() as c:
        assert c.execute("SELECT COUNT(*) FROM crypto_delivery_receipts").fetchone()[0] == 1
        assert first_crypto_delivery(c, trade_id) == receipt.date.isoformat()
    assert record_delivery(trade_id, 1, None) is False


def test_real_scan_binds_successful_send_to_saved_signal(monkeypatch, tmp_path):
    from bot import alerts
    from test_telegram_independent_alerts import idea
    db = configure_db(monkeypatch, tmp_path)
    monkeypatch.setattr(alerts, "get_subscribers", lambda: {1,2})
    monkeypatch.setattr(alerts, "alert_timeframes_for_run", lambda: (["4h"], []))
    async def no_forex(*args): return {}
    async def scan(*args): return [idea()], "hyperliquid", {}
    monkeypatch.setattr(alerts, "dispatch_pending_forex", no_forex)
    monkeypatch.setattr(alerts, "dispatch_pending_forex_limits", no_forex)
    monkeypatch.setattr(alerts, "scan_top_ideas", scan)
    class Bot:
        async def send_message(self, chat_id, text):
            if chat_id == 2: raise RuntimeError("failed")
            return SimpleNamespace(message_id=50, date=datetime.now(UTC)-timedelta(seconds=1))
    result = asyncio.run(alerts.run_alert_scan(Bot()))
    assert result["sent"] == 1
    with db.get_connection() as c:
        row = c.execute("SELECT id FROM trade_ideas").fetchone()
        assert first_crypto_delivery(c, row["id"])
        assert c.execute("SELECT COUNT(*) FROM crypto_delivery_receipts").fetchone()[0] == 1
