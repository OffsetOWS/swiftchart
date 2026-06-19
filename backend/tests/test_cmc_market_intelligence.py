from app.services.cmc import _asset_list, market_quality_score, quality_label


def test_market_quality_score_rewards_cap_volume_and_rank():
    btc_like = market_quality_score(2_000_000_000_000, 80_000_000_000, 1)
    small_asset = market_quality_score(20_000_000, 500_000, 700)

    assert btc_like > small_asset
    assert quality_label(btc_like) == "High Quality Asset"
    assert quality_label(small_asset) == "Lower Liquidity Risk"


def test_market_quality_score_is_bounded():
    assert 0 <= market_quality_score(0, 0, None) <= 100
    assert 0 <= market_quality_score(10**20, 10**20, 1) <= 100


def test_duplicate_symbol_prefers_ranked_canonical_asset():
    payload = {
        "BTC": [
            {
                "id": 31469,
                "name": "Imitation BTC",
                "symbol": "BTC",
                "cmc_rank": None,
                "quote": {"USD": {"market_cap": 0, "volume_24h": 0}},
            },
            {
                "id": 1,
                "name": "Bitcoin",
                "symbol": "BTC",
                "cmc_rank": 1,
                "quote": {"USD": {"market_cap": 2_000_000_000_000, "volume_24h": 80_000_000_000}},
            },
        ]
    }

    assets = _asset_list(payload)

    assert len(assets) == 1
    assert assets[0]["id"] == 1
