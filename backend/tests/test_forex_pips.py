import pytest

from app.forex.pips import pip_distance, pip_size_for_symbol, trade_pip_distances


def test_standard_pair_pip_size_and_distance():
    assert pip_size_for_symbol("EURUSD") == 0.0001
    assert pip_distance("EUR/USD", 1.0850, 1.0825) == 25.0
    assert pip_distance("GBPUSD", 1.2700, 1.2675) == 25.0


def test_jpy_pair_pip_size_and_distance():
    assert pip_size_for_symbol("USDJPY") == 0.01
    assert pip_distance("USD/JPY", 150.25, 149.95) == 30.0
    assert pip_distance("GBPJPY", 191.40, 190.90) == 50.0


def test_buy_trade_pip_distances():
    distances = trade_pip_distances(
        symbol="EURUSD",
        side="BUY",
        entry=1.0845,
        stop_loss=1.0812,
        take_profit_1=1.0881,
        take_profit_2=1.0915,
    )

    assert distances.stop_loss_pips == 33.0
    assert distances.take_profit_1_pips == 36.0
    assert distances.take_profit_2_pips == 70.0


def test_sell_trade_pip_distances():
    distances = trade_pip_distances(
        symbol="GBPUSD",
        side="SELL",
        entry=1.2700,
        stop_loss=1.2730,
        take_profit_1=1.2660,
        take_profit_2=1.2620,
    )

    assert distances.stop_loss_pips == 30.0
    assert distances.take_profit_1_pips == 40.0
    assert distances.take_profit_2_pips == 80.0


def test_metals_require_provider_precision():
    with pytest.raises(ValueError, match="Metals require"):
        pip_size_for_symbol("XAUUSD")

    assert pip_distance("XAUUSD", 2417.0, 2418.5, provider_pip_size=0.1) == 15.0


def test_invalid_or_missing_prices_raise():
    with pytest.raises(ValueError, match="Both prices are required"):
        pip_distance("EURUSD", None, 1.08)

    with pytest.raises(ValueError, match="Buy trades require"):
        trade_pip_distances(symbol="EURUSD", side="BUY", entry=1.0845, stop_loss=1.09, take_profit_1=1.088)
