from app.exchanges.variational import _normalize_symbol, normalize_candles


def test_variational_symbol_normalization():
    assert _normalize_symbol("BTC") == "BTCUSDT"
    assert _normalize_symbol("ETH-PERP") == "ETHUSDT"
    assert _normalize_symbol("SOL_USDC") == "SOLUSDT"


def test_variational_candle_normalization_from_dict_rows():
    df = normalize_candles(
        {
            "candles": [
                {"timestamp": "2026-01-01T00:00:00Z", "open": "100", "high": "110", "low": "95", "close": "105", "volume": "12.5"},
                {"timestamp": "2026-01-01T04:00:00Z", "open": "105", "high": "112", "low": "101", "close": "108", "volume": "9.2"},
            ]
        }
    )

    assert len(df) == 2
    assert list(df.columns) == ["timestamp", "open", "high", "low", "close", "volume"]
    assert float(df["close"].iloc[-1]) == 108


def test_variational_candle_normalization_from_list_rows():
    df = normalize_candles([[1767225600000, "100", "110", "95", "105", "12.5"]])

    assert len(df) == 1
    assert float(df["volume"].iloc[0]) == 12.5
