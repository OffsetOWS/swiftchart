import asyncio
from copy import deepcopy
from datetime import timedelta
import json

import pandas as pd
import pytest

from app.config import get_settings
from app.models.schemas import TradeStats, TradeHistoryPage
from app.services import trade_history, tracking_funding
from app.services.telegram_dispatch import mark_telegram_recipient_success, mark_telegram_recipient_failure
from app.services.tracking_ledger import first_crypto_delivery
from app.utils import database
from bot.formatter import format_stats, format_paper_trades
from test_tracking_corrections import START, bars, idea, run
from test_tracking_funding import rates


@pytest.fixture
def db(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///"+str(tmp_path/"canonical.db"))
    monkeypatch.setenv("ALERT_DEDUPE_STATE_PATH", str(tmp_path/"dedupe.json"))
    get_settings.cache_clear()
    monkeypatch.setattr(database,"_INITIALIZED",False)
    database.init_db()
    yield database
    get_settings.cache_clear()


def insert(db, identifier=1, *, mode="PRODUCTION", decision="TRADE", entry_status="READY"):
    with db.get_connection() as c:
        c.execute("""INSERT INTO trade_ideas
          (id,symbol,timeframe,exchange,direction,entry_zone_low,entry_zone_high,stop_loss,
           take_profit_1,take_profit_2,risk_reward,confidence,reason,invalidation,created_at,
           strategy_version,strategy_decision,outcome_tracking_mode,entry_status,opportunity_key,executable_at)
          VALUES (?,'BTCUSDT','4h','hyperliquid','LONG',99,101,90,110,120,2,80,'reason','stop',?,
                  'v1',?,?,?,?,?)""",(identifier,START.isoformat(),decision,mode,entry_status,"identity-"+str(identifier),START.isoformat()))
        c.execute("""INSERT INTO telegram_dispatches
          (id,trade_idea_id,opportunity_key,status,eligible_at,created_at,updated_at)
          VALUES (?,?,?,'ATTEMPTED',?,?,?)""",(identifier,identifier,"identity-"+str(identifier),*[START.isoformat()]*3))
        c.execute("""INSERT INTO telegram_dispatch_recipients
          (dispatch_id,chat_id,status,created_at,updated_at) VALUES (?,'101','ATTEMPTED',?,?)""",
          (identifier,START.isoformat(),START.isoformat()))
        return dict(c.execute("SELECT * FROM trade_ideas WHERE id=?",(identifier,)).fetchone())


def test_canonical_receipt_is_the_only_delivery_source_and_is_idempotent(db):
    insert(db)
    with db.get_connection() as c: assert first_crypto_delivery(c,1) is None
    mark_telegram_recipient_failure(1,101,"temporary",now=START)
    with db.get_connection() as c: assert first_crypto_delivery(c,1) is None
    mark_telegram_recipient_success(1,101,telegram_message_id=7,now=START)
    mark_telegram_recipient_success(1,101,telegram_message_id=8,now=START+timedelta(hours=1))
    mark_telegram_recipient_failure(1,101,"late callback",now=START+timedelta(hours=2))
    with db.get_connection() as c:
        assert first_crypto_delivery(c,1)==START.isoformat()
        receipt=c.execute("SELECT * FROM telegram_dispatch_recipients").fetchone()
        assert receipt["telegram_message_id"]==7 and receipt["status"]=="SENT"
        assert not c.execute("SELECT 1 FROM sqlite_master WHERE name='crypto_delivery_receipts'").fetchone()


def test_replay_preserves_identity_and_uses_canonical_cost_evidence(db, monkeypatch):
    row=insert(db)
    mark_telegram_recipient_success(1,101,telegram_message_id=7,now=START)
    async def candles(*args): return pd.DataFrame(bars((100,102,99,101),(105,111,101,110),(115,121,111,120)))
    async def funding(*args): return rates()
    monkeypatch.setattr(trade_history,"get_candles_cached",candles)
    monkeypatch.setattr(tracking_funding,"fetch_rates",funding)
    result=asyncio.run(trade_history._evaluate_rows([row]))
    assert result["errors"]==0 and result["funding_unknown"]==0
    record=TradeHistoryPage.model_validate(trade_history.query_trade_history({})).records[0]
    assert record.gross_r_multiple==1.5
    assert record.net_r_multiple==pytest.approx(1.38925)
    stats=TradeStats.model_validate(trade_history.stats())
    assert stats.expectancy_sample_count==1 and stats.expectancy_per_trade==1.39
    assert "Costed sample: 1" in format_stats(stats.model_dump())
    with db.get_connection() as c:
        after=dict(c.execute("SELECT * FROM trade_ideas WHERE id=1").fetchone())
        for key in ("opportunity_key","strategy_decision","outcome_tracking_mode","executable_at","entry_status"):
            assert after[key]==row[key]
        assert after["lifecycle_status"]=="closed"
        evidence=json.loads(c.execute("SELECT evidence_json FROM tracking_corrections").fetchone()[0])
        assert evidence["costs"]["rates"]==rates()


def test_production_stats_exclude_shadow_and_pending_retests_even_with_booked_r(db):
    rows=[insert(db,1),insert(db,2,mode="SHADOW",decision="SHADOW"),
          insert(db,3,decision="WAIT_FOR_RETEST",entry_status="WAIT_FOR_RETEST"),insert(db,4)]
    outcome=run(data=bars((100,102,99,101),(111,121,101,120)))
    outcome["tracking_evidence"]["costs"]={"funding_r":0,"fee_r":.01075}
    for row in rows:trade_history.update_outcome(row["id"],deepcopy(outcome))
    with db.get_connection() as c:
        c.execute("UPDATE trade_ideas SET retest_confirmed_at=? WHERE id=4",(START.isoformat(),))
    stats=trade_history.stats()
    assert stats["detected_ideas"]==4 and stats["pending_retest_count"]==1 and stats["shadow_count"]==1
    assert stats["expectancy_sample_count"]==2 and stats["full_win_count"]==2
    assert stats["net_r_total"]==2.98


def test_missing_funding_remains_unknown(db,monkeypatch):
    row=insert(db)
    mark_telegram_recipient_success(1,101,now=START)
    async def candles(*args): return pd.DataFrame(bars((100,102,99,101),(111,121,101,120)))
    async def funding(*args): return []
    monkeypatch.setattr(trade_history,"get_candles_cached",candles)
    monkeypatch.setattr(tracking_funding,"fetch_rates",funding)
    asyncio.run(trade_history._evaluate_rows([row]))
    record=trade_history.get_trade_history(1)
    assert record["net_r_multiple"] is None and record["cost_status"]=="UNKNOWN"
    stats=trade_history.stats()
    assert stats["expectancy_per_trade"] is None and stats["funding_unknown_count"]==1
    assert "Unknown costs" in format_paper_trades([{"pnl_r":5}])


def test_history_timeout_preserves_records_without_gross_or_zero_fallback(monkeypatch):
    from app.routes import paper_trades
    rows=[{"id":1,"exchange":"hyperliquid","pnl":4},{"id":2,"exchange":"forex","pnl":2}]
    async def blocked(*args): await asyncio.Event().wait()
    monkeypatch.setattr(paper_trades,"_refresh_crypto_paper_trade_outcomes",blocked)
    monkeypatch.setattr(paper_trades,"HISTORY_REFRESH_TIMEOUT_SECONDS",.001)
    result=asyncio.run(paper_trades._refresh_crypto_paper_trades(rows))
    assert len(result)==2 and result[0]["id"]==1
    assert result[0]["pnl"] is None and result[0]["net_r_multiple"] is None
    assert result[0]["cost_status"]=="UNKNOWN"
    assert rows[0]["pnl"]==4 and result[1]==rows[1]


def test_sixty_day_hourly_replay_requests_more_than_the_old_thousand_bar_limit(db,monkeypatch):
    from datetime import UTC,datetime
    row=insert(db)
    row.update(timeframe="1h",created_at=(datetime.now(UTC)-timedelta(days=60)).isoformat())
    requested=[]
    async def candles(exchange,symbol,timeframe,limit):
        requested.append(limit)
        return pd.DataFrame()
    monkeypatch.setattr(trade_history,"get_candles_cached",candles)
    monkeypatch.setattr(trade_history,"update_outcome",lambda *args:None)
    result=asyncio.run(trade_history._evaluate_rows([row]))
    assert result["errors"]==0
    assert len(requested)==1 and 1440<requested[0]<=5000


def test_review_summary_aggregates_scores_with_the_existing_null_as_zero_rule(db):
    with db.get_connection() as c:
        for accepted,base,adjusted in [(1,70,80),(1,None,60),(0,30,None)]:
            c.execute("""INSERT INTO signal_reviews
              (symbol,timeframe,exchange,direction,accepted,reason,base_score,adjusted_score,
               regime_score,regime_label,trend_alignment)
              VALUES ('BTCUSDT','4h','hyperliquid','LONG',?,'reason',?,?,0,'range','aligned')""",
              (accepted,base,adjusted))
    assert trade_history.stats()["accepted_vs_rejected"]==[
        {"status":"accepted","count":2,"average_base_score":35.0,"average_adjusted_score":70.0},
        {"status":"rejected","count":1,"average_base_score":30.0,"average_adjusted_score":0.0}]


def test_history_evidence_lookup_handles_more_than_sqlite_variable_limit(db):
    import sqlite3
    from app.services.tracking_ledger import ensure_tracking_ledger, record_correction
    with db.get_connection() as c:
        ensure_tracking_ledger(c)
        record_correction(c,"CRYPTO",1001,{}, {}, {"category":"unresolved","flags":["last-row"]},START)
        c.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER,999)
        records=trade_history._public_tracking_rows(c,[{"id":i} for i in range(1,1002)])
    assert len(records)==1001
    assert records[-1]["tracking_evidence"]["flags"]==["last-row"]
    assert all(row["net_r_multiple"] is None for row in records)
