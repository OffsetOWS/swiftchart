from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "SwiftChart"
    environment: str = "development"
    database_url: str = "sqlite:///./swiftchart.db"
    binance_base_url: str = "https://api.binance.com"
    hyperliquid_base_url: str = "https://api.hyperliquid.xyz"
    frontend_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    live_trading_enabled: bool = False
    default_exchange: str = "binance"
    default_timeframe: str = "4h"
    default_account_size: float = 10_000
    default_risk_per_trade: float = Field(default=1.0, ge=0.01, le=20)
    default_min_rr: float = Field(default=2.0, ge=0.1)
    default_max_open_trades: int = Field(default=3, ge=1)

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


SUPPORTED_TIMEFRAMES = ["30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d"]
DEFAULT_SCAN_LIST = [
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "BNBUSDT",
    "XRPUSDT",
    "DOGEUSDT",
    "AVAXUSDT",
    "LINKUSDT",
    "ARBUSDT",
    "OPUSDT",
]


@lru_cache
def get_settings() -> Settings:
    return Settings()
