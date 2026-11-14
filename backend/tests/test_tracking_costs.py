import pytest

from app.services.tracking_costs import cost_r, net_r


def row(status: str, pnl: float | None) -> dict:
    return {
        "status": status,
        "pnl_r_multiple": pnl,
        "entry_zone_low": 99.0,
        "entry_zone_high": 101.0,
        "stop_loss": 95.0,
        "take_profit_1": 105.0,
        "take_profit_2": 110.0,
    }


def test_stop_cost_uses_entry_and_stop_notional():
    # (100 + 95) * 10 bps / 5 risk
    assert cost_r(row("SL_HIT", -1.0), 10) == pytest.approx(0.039)
    assert net_r(row("SL_HIT", -1.0), 10) == pytest.approx(-1.039)


def test_tp2_cost_weights_both_exit_legs():
    # (100 + 0.5*105 + 0.5*110) * 5 bps / 5 risk
    assert cost_r(row("TP2_HIT", 1.5), 5) == pytest.approx(0.02075)


def test_zero_cost_leaves_r_unchanged():
    assert net_r(row("CLOSED_AFTER_TP1", 0.0), 0) == 0.0


def test_inverted_zone_is_normalized():
    inverted = row("SL_HIT", -1.0) | {"entry_zone_low": 101.0, "entry_zone_high": 99.0}
    assert cost_r(inverted, 10) == pytest.approx(0.039)


@pytest.mark.parametrize("status,pnl", [("OPEN", None), ("AMBIGUOUS", None), ("PARTIAL_WIN", 0.5), ("SL_HIT", None)])
def test_unresolved_or_legacy_rows_get_no_cost(status, pnl):
    assert cost_r(row(status, pnl), 10) is None
