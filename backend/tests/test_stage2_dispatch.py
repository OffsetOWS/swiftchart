import asyncio
from datetime import UTC,datetime,timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram.error import Forbidden

from app.config import get_settings
from app.services import telegram_dispatch as dispatch
from app.services.trade_history import save_trade_ideas
from app.services.tracking_ledger import first_crypto_delivery
from app.utils import database
from bot import alerts
from test_canonical_telegram_dispatch import configure_db,idea


def test_blocked_recipient_is_quarantined_across_the_claimed_batch_and_future_dispatches(monkeypatch,tmp_path):
    configure_db(monkeypatch,tmp_path,"101,202")
    monkeypatch.setattr(alerts,"_btc_context",AsyncMock(return_value=None))
    for symbol in ("BTCUSDT","ETHUSDT"):save_trade_ideas([idea(symbol=symbol)])
    sent=[];blocked=[]
    async def send_message(chat_id,**kwargs):
        if chat_id==202:
            blocked.append(chat_id);raise Forbidden("bot was blocked by the user")
        sent.append(chat_id);return SimpleNamespace(message_id=len(sent))
    result=asyncio.run(alerts.dispatch_crypto_outbox(SimpleNamespace(send_message=send_message)))
    assert blocked==[202] and sent==[101,101]
    assert result["sent"]==2 and result["failed"]==1
    with database.get_connection() as c:
        assert {r[0] for r in c.execute("SELECT status FROM telegram_dispatches")}=={"PARTIAL_FINAL"}
        assert all(r[0]=="FAILED_PERMANENT" for r in c.execute("SELECT status FROM telegram_dispatch_recipients WHERE chat_id='202'"))
    save_trade_ideas([idea(symbol="SOLUSDT")])
    asyncio.run(alerts.dispatch_crypto_outbox(SimpleNamespace(send_message=send_message)))
    assert blocked==[202] and len(sent)==3
    dispatch.clear_crypto_recipient_block(202)
    save_trade_ideas([idea(symbol="AVAXUSDT")])
    # Old terminal receipts stay terminal; explicit resubscription admits only new work.
    assert all(c.idea.symbol=="AVAXUSDT" for c in dispatch.claim_telegram_dispatches([101,202]))


def test_known_blocked_backlog_does_not_starve_a_new_signal_after_migration(monkeypatch,tmp_path):
    configure_db(monkeypatch,tmp_path,"101,202")
    for index in range(51):save_trade_ideas([idea(symbol=f"OLD{index}USDT")])
    claimed=dispatch.claim_telegram_dispatches([101,202],limit=250)
    with database.get_connection() as c:
        c.execute("CREATE TABLE forex_stage2_sentinel(id INTEGER PRIMARY KEY,value TEXT)")
        c.execute("INSERT INTO forex_stage2_sentinel VALUES (1,'unchanged')")
        c.execute("UPDATE telegram_dispatch_recipients SET status='SENT',sent_at=? WHERE chat_id='101'",(datetime.now(UTC).isoformat(),))
        c.execute("UPDATE telegram_dispatch_recipients SET status='FAILED_RETRYABLE',attempt_count=49,last_error='Forbidden: bot was blocked by the user' WHERE chat_id='202'")
        c.execute("UPDATE telegram_dispatches SET status='PARTIAL'")
    newest=save_trade_ideas([idea(symbol="NEWUSDT")])[0]
    # An attempted lease left over from shutdown is also migrated.
    with database.get_connection() as c:c.execute("UPDATE telegram_dispatch_recipients SET status='ATTEMPTED' WHERE dispatch_id=? AND chat_id='202'",(claimed[0].dispatch_id,))
    result=dispatch.migrate_permanent_telegram_failures()
    assert result=={"blocked_crypto_recipients":1,"permanent_failure_rows":51}
    assert dispatch.migrate_permanent_telegram_failures()=={"blocked_crypto_recipients":0,"permanent_failure_rows":0}
    available=dispatch.claim_telegram_dispatches([101,202],limit=1)
    assert len(available)==1 and available[0].trade_idea_id==newest and available[0].recipient_chat_ids==(101,)
    with database.get_connection() as c:
        assert tuple(c.execute("SELECT * FROM forex_stage2_sentinel").fetchone())==(1,"unchanged")
        assert c.execute("SELECT COUNT(*) FROM telegram_dispatch_recipients WHERE status='SENT'").fetchone()[0]==51


@pytest.mark.parametrize("error,expected",[("Forbidden: blocked by the user","BLOCKED"),
    ("BadRequest: Chat not found","CHAT_NOT_FOUND"),("Forbidden: user is deactivated","DEACTIVATED"),
    ("BadRequest: Message is too long",None),("TimedOut: request timeout",None)])
def test_terminal_failure_classification_does_not_quarantine_transient_or_payload_errors(error,expected):
    assert dispatch.permanent_telegram_failure_reason(error)==expected


def test_rate_limit_due_time_does_not_fill_the_head_of_the_queue(monkeypatch,tmp_path):
    configure_db(monkeypatch,tmp_path)
    first=save_trade_ideas([idea()])[0];now=datetime.now(UTC)
    candidate=dispatch.claim_telegram_dispatches([101],now=now)[0]
    dispatch.mark_telegram_recipient_failure(candidate.dispatch_id,101,"RetryAfter",now=now,retry_after_seconds=600)
    second=save_trade_ideas([idea(symbol="ETHUSDT")])[0]
    available=dispatch.claim_telegram_dispatches([101],limit=1,now=now+timedelta(seconds=30))
    assert [row.trade_idea_id for row in available]==[second]
    available=dispatch.claim_telegram_dispatches([101],now=now+timedelta(seconds=601))
    assert first in [row.trade_idea_id for row in available]


def test_late_failure_does_not_cancel_a_confirmed_receipt_or_quarantine_its_recipient(monkeypatch,tmp_path):
    configure_db(monkeypatch,tmp_path)
    identifier=save_trade_ideas([idea()])[0]
    candidate=dispatch.claim_telegram_dispatches([101])[0];now=datetime.now(UTC)
    dispatch.mark_telegram_recipient_success(candidate.dispatch_id,101,telegram_message_id=7,now=now)
    dispatch.mark_telegram_recipient_failure(candidate.dispatch_id,101,"Forbidden: bot was blocked by the user")
    with database.get_connection() as c:
        assert first_crypto_delivery(c,identifier)==now.replace(microsecond=0).isoformat()
        assert c.execute("SELECT COUNT(*) FROM telegram_dispatch_recipient_blocks").fetchone()[0]==0


def test_crypto_poll_ignores_legacy_half_hour_setting_and_does_not_poll_forex(monkeypatch):
    monkeypatch.setenv("ALERT_SCAN_INTERVAL_SECONDS","1800")
    monkeypatch.setenv("CRYPTO_TELEGRAM_DISPATCH_INTERVAL_SECONDS","30")
    get_settings.cache_clear();waits=[]
    async def sleep(seconds):
        waits.append(seconds)
        if len(waits)==2:raise asyncio.CancelledError()
    async def crypto(_):return {"sent":0}
    monkeypatch.setattr(alerts.asyncio,"sleep",sleep)
    monkeypatch.setattr(alerts,"monotonic",lambda:100)
    monkeypatch.setattr(alerts,"dispatch_crypto_outbox",crypto)
    forex=AsyncMock(side_effect=AssertionError("Crypto poll must not run Forex"))
    monkeypatch.setattr(alerts,"dispatch_pending_forex",forex)
    with pytest.raises(asyncio.CancelledError):asyncio.run(alerts.alert_loop(None))
    assert waits==[20,30]
    forex.assert_not_awaited();get_settings.cache_clear()
