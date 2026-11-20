from copy import deepcopy
from datetime import UTC, datetime, timedelta
import asyncio
import pytest

from app.services.tracking_funding import fetch_rates, funding_r
from app.services.tracking_costs import net_r
from test_tracking_corrections import idea, bars, run, START


def trade():
    data = bars((100,102,99,101),(105,111,101,110),(115,121,111,120))
    return idea(symbol='BTCUSDT', exchange='hyperliquid'), run(data=data)


def rates():
    return [dict(coin='BTC', time=int((START+timedelta(hours=i)).timestamp()*1000), fundingRate='0.001')
            for i in range(4,16)]


def test_funding_weights_remaining_quantity_and_direction():
    row, outcome = trade()
    # Full quantity at 04..11, half quantity at 12..15; exit at 16 excluded.
    assert funding_r(row, outcome, rates()) == pytest.approx(.1)
    assert funding_r(dict(row, direction='SHORT'), outcome, rates()) == pytest.approx(-.1)
    negative = [dict(r, fundingRate='-0.001') for r in rates()]
    assert funding_r(row, outcome, negative) == pytest.approx(-.1)


@pytest.mark.parametrize('change', ['missing','wrong_coin','nonfinite','conflict'])
def test_unknown_funding_does_not_become_zero(change):
    row, outcome = trade()
    history = rates()
    if change == 'missing': history.pop()
    if change == 'wrong_coin': history[0]['coin'] = 'ETH'
    if change == 'nonfinite': history[0]['fundingRate'] = 'nan'
    if change == 'conflict': history.append(dict(history[0], fundingRate='0.2'))
    assert funding_r(row, outcome, history) is None
    outcome['tracking_evidence']['costs'] = dict(funding_r=None)
    assert net_r(dict(row, **outcome),5) is None


def test_funding_uses_refined_fill_times_instead_of_coarse_mapped_exit():
    row, outcome = trade()
    outcome = deepcopy(outcome)
    outcome['tracking_evidence']['intrabar'] = dict(entry_at=(START+timedelta(hours=4.25)).isoformat())
    # First funding is now 05:00 rather than the parent bar's 04:00.
    assert funding_r(row, outcome, rates()[1:]) == pytest.approx(.09)


def test_funding_daily_requests_are_cached_and_bounded(monkeypatch):
    from app.services import tracking_funding
    calls=[]
    async def post(self,payload):
        calls.append(payload)
        return rates()
    monkeypatch.setattr(tracking_funding.HyperliquidClient, '_post_info', post)
    row, outcome = trade()
    cache={}
    async def run_twice():
        assert await fetch_rates(row,outcome,cache) == rates()
        assert await fetch_rates(row,outcome,cache) == rates()
    asyncio.run(run_twice())
    assert len(calls) == 1
    assert calls[0]['type'] == 'fundingHistory'
    assert calls[0]['endTime']-calls[0]['startTime'] == 86_400_000-1


def test_no_funding_tick_in_exposure_is_verified_zero():
    row, outcome = trade()
    outcome['tracking_evidence']['validated_entry_at'] = (START+timedelta(hours=4.25)).isoformat()
    outcome['closed_at'] = (START+timedelta(hours=4.5)).isoformat()
    assert funding_r(row,outcome,[]) == 0
