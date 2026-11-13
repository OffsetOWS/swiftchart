from datetime import UTC, datetime, timedelta
import json
import sqlite3

import pytest

from app.services.tracking_rules import replay_crypto, forex_provider_evidence, zone_bounds
from app.services.tracking_ledger import record_correction, guard_crypto_transition, first_crypto_delivery

START = datetime(2026, 9, 10, tzinfo=UTC)


def idea(**kw):
    return dict(dict(id=1, direction="LONG",timeframe="4h",created_at=START.isoformat(),
        entry_zone_low=99,entry_zone_high=101,stop_loss=90,take_profit_1=110,
        take_profit_2=120,risk_reward=99), **kw)


def bars(*prices):
    return [dict(timestamp=START+timedelta(hours=4*(i+1)),open=o,high=h,low=l,close=c)
            for i,(o,h,l,c) in enumerate(prices)]


def run(row=None, data=None, **kw):
    options=dict(first_delivery=START+timedelta(minutes=1),as_of=START+timedelta(hours=60),expiry_bars=3)
    options.update(kw)
    return replay_crypto(row or idea(), data or bars((100,102,99,101),(102,105,101,103),(103,104,101,102)), **options)


def test_filled_expiry_does_not_become_no_entry():
    result=run()
    assert result['status']=='EXPIRED_POSITION'
    assert result['entry_triggered_at'] and result['pnl_r_multiple'] is None
    assert result['tracking_evidence']['category']=='unresolved'


def test_inverted_zone_normalized_without_modifying_input():
    row=idea(entry_zone_low=101,entry_zone_high=99)
    result=run(row)
    assert zone_bounds(101,99)==(99,101)
    assert result['tracking_evidence']['validated_entry_at']
    assert row['entry_zone_low']==101
    assert 'INVERTED_ENTRY_ZONE' in result['tracking_evidence']['flags']


def test_zone_touch_without_midpoint_is_not_fill():
    result=run(data=bars(*[(98,99.5,97,99)]*3))
    assert result['result']=='NO_ENTRY'
    assert not result['entry_triggered_at']
    assert result['pnl_r_multiple'] is None


def test_bad_historical_entry_is_preserved_but_not_counted():
    claim=(START+timedelta(hours=4)).isoformat()
    result=run(idea(entry_triggered_at=claim),bars(*[(98,99.5,97,99)]*3))
    assert result['entry_triggered_at']==claim
    assert result['result']=='UNRESOLVED'
    assert result['tracking_evidence']['category']=='unresolved'


def test_entry_must_be_after_delivery_and_completed():
    result=run(data=bars((100,102,99,101)),first_delivery=START+timedelta(hours=5),as_of=START+timedelta(hours=8))
    assert not result['tracking_evidence']['validated_entry_at']
    incomplete=run(as_of=START+timedelta(hours=7))
    assert not incomplete['tracking_evidence']['validated_entry_at']


@pytest.mark.parametrize('high,low', [(121,99),(102,89),(121,89)])
def test_entry_and_exit_same_candle_unresolved(high,low):
    result=run(data=bars((100,high,low,100)))
    assert result['status']=='AMBIGUOUS' and result['pnl_r_multiple'] is None


def test_tp2_uses_half_at_each_target_not_advertised_rr():
    result=run(data=bars((100,102,99,101),(105,121,101,120)))
    assert result['pnl_r_multiple']==1.5
    assert [(f['kind'],f['quantity'],f['price']) for f in result['tracking_evidence']['fills']]==[('TP1',.5,110),('TP2',.5,120)]


def test_short_is_mirrored_and_partial_stop_is_net_not_full_loss():
    result=run(idea(direction='SHORT',stop_loss=110,take_profit_1=90,take_profit_2=80),
               bars((100,101,99,100),(95,99,89,90),(105,111,95,110)))
    assert result['status']=='CLOSED_AFTER_TP1'
    assert result['pnl_r_multiple']==0


def test_tp1_expiry_keeps_runner_unresolved_and_booked_half_separate():
    result=run(data=bars((100,102,99,101),(105,111,101,110),(105,109,101,108)))
    assert result['status']=='EXPIRED_RUNNER' and result['pnl_r_multiple'] is None
    assert result['tracking_evidence']['booked_r']==.5
    assert result['tracking_evidence']['remaining_quantity']==.5


def test_same_bar_tp1_and_stop_after_entry_ambiguous():
    result=run(data=bars((100,102,99,101),(100,111,89,100)))
    assert result['status']=='AMBIGUOUS'


def test_missing_bar_not_used_to_extend_horizon():
    data=bars(*[(100,102,99,100)]*4)
    del data[1]
    result=run(data=data)
    assert 'MISSING_COMPLETED_CANDLE' in result['tracking_evidence']['flags']


def test_missing_delivery_does_not_fall_back_to_creation():
    result=run(first_delivery=None)
    assert result['status']=='UNRESOLVED'
    assert 'MISSING_DELIVERY_EVIDENCE' in result['tracking_evidence']['flags']


def test_transition_guard_preserves_entry_and_prior_events():
    claim=(START+timedelta(hours=4)).isoformat()
    before=idea(entry_triggered_at=claim,lifecycle_events=json.dumps([{'event':'ENTRY_TRIGGERED','at':claim}]))
    attempted=run(data=bars(*[(98,99.5,97,99)]*3))
    guarded=guard_crypto_transition(before,attempted)
    assert guarded['result']=='UNRESOLVED' and guarded['entry_triggered_at']==claim
    assert 'ENTRY_TRIGGERED' in guarded['lifecycle_events']


def test_ledger_append_only_idempotent_and_preserves_original():
    c=sqlite3.connect(':memory:');c.row_factory=sqlite3.Row
    before=idea();after=run();evidence=after['tracking_evidence']
    first=record_correction(c,'CRYPTO',1,before,after,evidence,START)
    assert record_correction(c,'CRYPTO',1,before,after,evidence,START)==first
    assert json.loads(c.execute('SELECT original_json FROM tracking_corrections').fetchone()[0])==before
    for sql in ['DELETE FROM tracking_corrections','UPDATE tracking_corrections SET signal_id=2']:
        with pytest.raises(sqlite3.IntegrityError):c.execute(sql)
    assert first_crypto_delivery(c,1) is None


def test_forex_conflicting_provider_outcome_not_cherry_picked():
    row=dict(direction='SHORT',activated_at=START,stop_loss=101,take_profit_1=99,take_profit_2=98,status='TP2_HIT')
    data=[dict(provider=p,complete=1,candle_close_at=START+timedelta(hours=h),close=price)
          for p,h,price in [('oanda',4,97),('twelvedata',2,102)]]
    result=forex_provider_evidence(row,data,as_of=START+timedelta(hours=4))
    assert result['unresolved'] and 'PROVIDER_DISAGREEMENT' in result['flags']
    assert 'PROVIDER_COVERAGE_NOT_ESTABLISHED' in result['flags']
    assert result['provider_results']['oanda']['status']=='TP2_HIT'
    assert result['provider_results']['twelvedata']['status']=='STOPPED'


def test_forex_missing_provider_or_coverage_unresolved():
    row=dict(direction='LONG',activated_at=START,stop_loss=90,take_profit_1=110,take_profit_2=120,status='STOPPED')
    assert forex_provider_evidence(row,[],as_of=START+timedelta(hours=4))['unresolved']


def test_forming_candle_cannot_freeze_terminal_result():
    data=bars((100,102,99,100),(105,121,101,120))
    result=run(data=data,as_of=START+timedelta(hours=10))
    assert result['status']=='OPEN' and result['tp2_hit_at'] is None


def test_runner_stop_and_tp2_same_bar_is_unresolved():
    result=run(data=bars((100,102,99,100),(105,111,101,110),(100,121,89,100)))
    assert result['status']=='AMBIGUOUS' and result['pnl_r_multiple'] is None
    assert result['tracking_evidence']['booked_r']==.5


def test_maturity_uses_full_horizon_not_fast_resolution():
    result=run(data=bars((100,102,99,100),(100,105,89,90)),as_of=START+timedelta(hours=12))
    assert result['status']=='SL_HIT'
    assert not result['tracking_evidence']['mature']  # 3-bar horizon ends at 16:00.


def test_no_entry_without_all_horizon_candles_is_not_proven():
    result=run(data=bars((98,99,97,98)))
    assert result['status']=='UNRESOLVED'


def test_ledger_transaction_rolls_back_with_projection_failure():
    c=sqlite3.connect(':memory:');c.row_factory=sqlite3.Row
    from app.services.tracking_ledger import ensure_tracking_ledger
    ensure_tracking_ledger(c);c.commit()
    with pytest.raises(RuntimeError):
        with c:
            record_correction(c,'CRYPTO',1,idea(),run(),{},START)
            raise RuntimeError('projection failed')
    assert c.execute('SELECT count(*) FROM tracking_corrections').fetchone()[0]==0


def test_delivery_lookup_uses_first_successful_recipient_not_queue_time():
    c=sqlite3.connect(':memory:');c.row_factory=sqlite3.Row
    c.execute('CREATE TABLE telegram_dispatches (id INTEGER, trade_idea_id INTEGER)')
    c.execute('CREATE TABLE telegram_dispatch_recipients (dispatch_id INTEGER, sent_at TEXT)')
    c.execute('INSERT INTO telegram_dispatches VALUES (4,1)')
    c.executemany('INSERT INTO telegram_dispatch_recipients VALUES (4,?)',[(None,),('2026-09-10T08:10:00+00:00',),('2026-09-10T08:01:00+00:00',)])
    assert first_crypto_delivery(c,1)=='2026-09-10T08:01:00+00:00'


def test_crypto_projection_preserves_before_image_and_rejects_stale_worker(monkeypatch,tmp_path):
    from types import SimpleNamespace
    from app.utils import database
    from app.services.trade_history import update_outcome
    monkeypatch.setattr(database,'get_settings',lambda:SimpleNamespace(database_url='sqlite:///'+str(tmp_path/'tracking.db')))
    monkeypatch.setattr(database,'_INITIALIZED',False)
    claim=(START+timedelta(hours=4)).isoformat()
    with database.get_connection() as c:
        c.execute("""INSERT INTO trade_ideas
            (id,symbol,timeframe,exchange,direction,entry_zone_low,entry_zone_high,stop_loss,take_profit_1,take_profit_2,risk_reward,confidence,reason,invalidation,created_at,entry_triggered_at)
            VALUES (1,'BTCUSDT','4h','hyperliquid','LONG',99,101,90,110,120,2,80,'reason','stop',?,?)""",(START.isoformat(),claim))
    attempted=run(data=bars(*[(98,99.5,97,99)]*3))
    update_outcome(1,attempted)
    with database.get_connection() as c:
        row=dict(c.execute('SELECT * FROM trade_ideas WHERE id=1').fetchone())
        assert row['result']=='UNRESOLVED' and row['entry_triggered_at']==claim
        log=c.execute('SELECT original_json FROM tracking_corrections').fetchone()
        assert json.loads(log[0])['entry_triggered_at']==claim
        before_count=c.execute('SELECT count(*) FROM tracking_corrections').fetchone()[0]
    stale=dict(attempted,outcome_checked_at=START.isoformat())
    update_outcome(1,stale)
    with database.get_connection() as c:
        assert c.execute('SELECT count(*) FROM tracking_corrections').fetchone()[0]==before_count


def test_invalid_legacy_claim_is_corrected_but_confirmed_entry_is_not_rewritten():
    claim=(START+timedelta(hours=4)).isoformat()
    raw=idea(entry_triggered_at=claim)
    data=bars((98,99.5,97,99),(100,102,99,101),(105,121,101,120))
    result=run(raw,data)
    assert result['entry_triggered_at']==(START+timedelta(hours=8)).isoformat()
    assert result['tracking_evidence']['original_entry_claim']==claim
    assert guard_crypto_transition(raw,result)['status']=='TP2_HIT'
    confirmed=dict(raw,lifecycle_events=json.dumps([{'event':'ENTRY_CONFIRMED','at':claim}]))
    guarded=guard_crypto_transition(confirmed,result)
    assert guarded['status']=='UNRESOLVED' and guarded['entry_triggered_at']==claim
