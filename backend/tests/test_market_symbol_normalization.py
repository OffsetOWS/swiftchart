from app.routes.markets import _base_asset_symbol, _symbol_for_exchange


def test_base_asset_symbol_accepts_common_market_formats():
    assert _base_asset_symbol("btc") == "BTC"
    assert _base_asset_symbol("BTC/USDT") == "BTC"
    assert _base_asset_symbol("btc-usd") == "BTC"
    assert _base_asset_symbol("BEATSUSD") == "BEAT"
    assert _base_asset_symbol("LITUSD") == "LIT"


def test_hyperliquid_symbol_uses_usdt_market_notation():
    assert _symbol_for_exchange("hyperliquid", "LITUSD") == "LITUSDT"
    assert _symbol_for_exchange("hyperliquid", "BTC/USDT") == "BTCUSDT"


def test_other_exchange_keeps_original_market_notation():
    assert _symbol_for_exchange("variational", "BEATSUSD") == "BEATSUSD"
