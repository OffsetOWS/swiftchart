from __future__ import annotations

import asyncio
import warnings

import pandas as pd

from app.exchanges.hyperliquid import HyperliquidClient


def test_hyperliquid_candle_timestamp_conversion_is_future_safe(monkeypatch):
    async def run():
        client = HyperliquidClient()

        async def fake_post_info(payload):
            return [
                {"t": 1710000000000, "o": "100", "h": "101", "l": "99", "c": "100.5", "v": "1000"},
                {"t": 1710003600000, "o": "100.5", "h": "102", "l": "100", "c": "101", "v": "1100"},
            ]

        monkeypatch.setattr(client, "_post_info", fake_post_info)

        with warnings.catch_warnings():
            warnings.simplefilter("error", FutureWarning)
            frame = await client.get_candles("BTCUSDT", "1h", limit=2)

        assert pd.api.types.is_datetime64_any_dtype(frame["timestamp"])
        assert str(frame["timestamp"].dt.tz) == "UTC"
        assert frame["close"].tolist() == [100.5, 101.0]

    asyncio.run(run())
