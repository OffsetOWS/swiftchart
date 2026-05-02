from abc import ABC, abstractmethod

import pandas as pd


class ExchangeClient(ABC):
    name: str

    @abstractmethod
    async def get_markets(self) -> list[dict]:
        raise NotImplementedError

    @abstractmethod
    async def get_candles(self, symbol: str, timeframe: str, limit: int = 300) -> pd.DataFrame:
        raise NotImplementedError
