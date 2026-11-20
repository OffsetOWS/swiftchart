from copy import deepcopy
from datetime import timedelta
import pytest

from test_tracking_corrections import START, bars, idea, run
from app.services.tracking_costs import cost_r
from app.services.tracking_time_exit import apply_time_exit


def test_full_exposure_exits_at_horizon_close_without_changing_audited_result():
    data = bars((100,102,99,101),(102,105,101,103),(103,104,101,102))
    original = run(data=data)
    before = deepcopy(original)
    outcome = apply_time_exit(idea(), original, data)
    assert original == before
    assert original["status"] == "EXPIRED_POSITION"
    assert outcome["status"] == "TIME_EXIT"
    assert outcome["closed_at"] == (START + timedelta(hours=16)).isoformat()
    assert outcome["pnl_r_multiple"] == pytest.approx(.2)
    assert outcome["tracking_evidence"]["remaining_quantity"] == 0
    assert cost_r(dict(idea(), **outcome), 5) == pytest.approx(.0101)


def test_partial_exposure_only_liquidates_the_remaining_half():
    data = bars((100,102,99,101),(105,111,101,110),(105,109,101,108))
    outcome = apply_time_exit(idea(), run(data=data), data)
    assert outcome["pnl_r_multiple"] == pytest.approx(.9)
    assert [f["quantity"] for f in outcome["tracking_evidence"]["fills"]] == [.5, .5]
    assert cost_r(dict(idea(), **outcome), 5) == pytest.approx(.01045)


def test_short_time_exit_is_directional():
    row = idea(direction="SHORT", stop_loss=110, take_profit_1=90, take_profit_2=80)
    data = bars((100,102,99,101),(99,101,95,98),(98,99,94,95))
    outcome = apply_time_exit(row, run(row, data), data)
    assert outcome["pnl_r_multiple"] == .5


@pytest.mark.parametrize("kwargs", [dict(first_delivery=None), dict(as_of=START + timedelta(hours=10))])
def test_no_time_exit_without_entry_or_completed_horizon(kwargs):
    data = bars((100,102,99,101),(105,111,101,110),(105,109,101,108))
    outcome = run(data=data, **kwargs)
    assert apply_time_exit(idea(), outcome, data) is outcome


def test_missing_or_ambiguous_candles_do_not_become_time_exits():
    for data in [bars((100,121,89,100)), bars((100,102,99,101))]:
        original = run(data=data)
        assert apply_time_exit(idea(), original, data) is original
