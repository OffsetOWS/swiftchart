from app.services.tracking_statistics import summarize, grouped
from app.services.tracking_time_exit import apply_time_exit
from app.services.tracking_costs import cost_r
from test_tracking_corrections import idea, bars, run
import pytest


def resolved(data):
    row = idea()
    result = dict(row, **apply_time_exit(row, run(data=data), data))
    result['tracking_evidence']['costs'] = dict(fee_r=cost_r(result,5), funding_r=0)
    return result


def test_expectancy_excludes_unentered_open_unresolved_and_legacy_rows():
    win = resolved(bars((100,102,99,101),(111,121,101,120)))
    loss = resolved(bars((100,102,99,101),(100,105,89,90)))
    no_entry = dict(idea(), **run(data=bars(*[(98,99,97,98)]*3)))
    unresolved = dict(idea(), **run(first_delivery=None))
    legacy = dict(win, tracking_evidence={})
    result = summarize([win, loss, no_entry, unresolved, legacy], 5)
    assert result['expectancy_sample_count'] == 2
    assert result['expectancy_per_trade'] == pytest.approx(.24)
    assert result['legacy_unreplayed_count'] == 1
    assert result['full_win_count'] == result['win_count'] == 1
    assert result['no_entry_count'] == 1
    assert grouped([win, loss, no_entry], 'direction', 5)[0]['sample_count'] == 2


def test_partial_and_time_exit_wins_are_separate_from_full_target_wins():
    partial = resolved(bars((100,102,99,101),(105,111,101,110),(105,109,101,108)))
    timed = resolved(bars((100,102,99,101),(102,105,101,103),(103,104,101,102)))
    result = summarize([partial, timed], 5)
    assert result['full_win_count'] == result['win_count'] == 0
    assert result['partial_win_count'] == 1
    assert result['time_exit_win_count'] == 1
    assert result['tp1_then_expiry_count'] == 1


def test_missing_costs_are_not_silently_zero_in_expectancy():
    trade = resolved(bars((100,102,99,101),(111,121,101,120)))
    trade['status'] = 'LEGACY_UNKNOWN'
    result = summarize([trade], 5)
    assert result['resolved_entered_count'] == 1
    assert result['expectancy_sample_count'] == 0
    assert result['expectancy_per_trade'] is None
    assert result['net_r_total'] is None
