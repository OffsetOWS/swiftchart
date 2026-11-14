from app.services.liquidity_filter import filter_liquid_perp_markets, is_liquid_perp_market, perp_volume_24h


def test_perp_volume_24h_reads_exchange_volume_fields():
    assert perp_volume_24h({"perpVolume24h": "125000.5"}) == 125000.5
    assert perp_volume_24h({"dayNtlVlm": "99000"}) == 99000
    assert perp_volume_24h({"volume": 101000}) == 101000


def test_is_liquid_perp_market_requires_minimum_volume():
    assert is_liquid_perp_market({"symbol": "BTCUSDT", "perpVolume24h": 100000}, min_volume=100000)
    assert not is_liquid_perp_market({"symbol": "THINUSDT", "perpVolume24h": 99999.99}, min_volume=100000)
    assert not is_liquid_perp_market({"symbol": "UNKNOWNUSDT"}, min_volume=100000)


def test_filter_liquid_perp_markets_excludes_low_volume_symbols(caplog):
    caplog.set_level("INFO")
    markets = [
        {"symbol": "BTCUSDT", "perpVolume24h": 250000},
        {"symbol": "THINUSDT", "perpVolume24h": 50000},
    ]

    assert filter_liquid_perp_markets(markets, min_volume=100000) == [markets[0]]
    assert "Skipping THINUSDT: perp volume below $100,000" in caplog.text


def test_invalid_or_infinite_volume_is_not_admitted():
    for value in [float("nan"),float("inf"),-1,None]:
        assert not is_liquid_perp_market({"perpVolume24h":value},min_volume=250000)


def test_raised_floor_includes_boundary_and_excludes_unknown_volume():
    assert is_liquid_perp_market({"perpVolume24h":250000},min_volume=250000)
    assert not is_liquid_perp_market({"perpVolume24h":249999.99},min_volume=250000)
    assert not is_liquid_perp_market({},min_volume=250000)
