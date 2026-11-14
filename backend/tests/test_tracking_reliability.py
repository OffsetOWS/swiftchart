"""Regression cases found in the October reliability review."""
import json
import pytest
from test_tracking_corrections import idea, bars, run, START
from app.services.tracking_ledger import guard_crypto_transition
from app.services.tracking_rules import forex_provider_evidence
from app.services.tracking_rules import account_exit_legs


def test_quantity_accounting_uses_actual_legs_not_tp2_label():
    result=account_exit_legs(100,90,'LONG',[{'quantity':.25,'price':110},{'quantity':.5,'price':120}])
    assert result=={'booked_r':1.25,'remaining_quantity':.25}
    with pytest.raises(ValueError):
        account_exit_legs(100,90,'LONG',[{'quantity':1.5,'price':120}])


def test_event_only_confirmed_entry_survives_projection_regression():
    claim = bars((100,102,99,101))[0]['timestamp'].isoformat()
    before = dict(entry_triggered_at=None, lifecycle_events=json.dumps([
        dict(event='ENTRY_CONFIRMED', at=claim)]))
    after = run(data=bars(*[(98,99.5,97,99)]*3))
    guarded = guard_crypto_transition(before, after)
    assert guarded['result'] == 'UNRESOLVED'
    assert guarded['entry_triggered_at'] == claim


@pytest.mark.parametrize('complete', ['false', 'False', None, 'unknown'])
def test_unverified_complete_flag_is_not_a_fill(complete):
    data = bars((100,102,99,101),(105,121,101,120))
    data[0]['complete'] = complete
    result = run(data=data)
    assert result['pnl_r_multiple'] is None
    assert result['tracking_evidence']['category'] == 'unresolved'


@pytest.mark.parametrize('bad', [None, 'bad', float('nan')])
def test_invalid_candle_price_is_unresolved_not_exception(bad):
    data = bars((100,102,99,101))
    data[0]['low'] = bad
    result = run(data=data)
    assert result['tracking_evidence']['category'] == 'unresolved'


def test_invalid_direction_cannot_silently_be_short():
    result = run(idea(direction='UNKNOWN', stop_loss=110, take_profit_1=90, take_profit_2=80),
                 bars((100,101,99,100),(95,99,79,80)))
    assert result['pnl_r_multiple'] is None


def test_delivery_straddling_touch_cannot_prove_no_entry():
    from datetime import timedelta
    result = run(data=bars((100,102,99,101),(103,105,102,104),(103,105,102,104)),
                 first_delivery=START+timedelta(hours=5))
    assert result['result'] == 'UNRESOLVED'
    assert 'DELIVERY_BAR_ORDER_UNKNOWN' in result['tracking_evidence']['flags']


def test_provider_same_timestamp_conflict_preserved():
    row = dict(activated_at=START.isoformat(), direction='LONG', stop_loss=90,
               take_profit_1=110, take_profit_2=120, tp1_closes_position=False)
    data = [dict(provider='OANDA', complete=True, candle_close_at='2026-09-10T04:00:00+00:00', close=p)
            for p in [89,121]]
    result = forex_provider_evidence(row,data,as_of='2026-09-11T00:00:00+00:00')
    assert result['unresolved']
    assert 'CONFLICTING_PROVIDER_CANDLES' in result['flags']


def test_rejected_replay_does_not_leave_validated_fills_in_projection():
    before = dict(entry_triggered_at='2026-09-10T08:00:00+00:00',
                  lifecycle_events=json.dumps([dict(event='ENTRY_CONFIRMED',at='2026-09-10T08:00:00+00:00')]))
    after = run(data=bars((100,102,99,101),(105,121,101,120)))
    result = guard_crypto_transition(before,after)
    assert result['pnl_r_multiple'] is None
    assert result['tracking_evidence']['validated_entry_at'] is None
    assert result['tracking_evidence']['rejected_replay']['fills']
