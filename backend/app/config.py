from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "SwiftChart"
    environment: str = "development"
    database_url: str = "sqlite:///./swiftchart.db"
    hyperliquid_base_url: str = "https://api.hyperliquid.xyz"
    variational_api_base_url: str = "https://omni-client-api.prod.ap-northeast-1.variational.io"
    variational_api_key: str = ""
    variational_enabled: bool = True
    variational_candles_path: str = "/candles"
    frontend_origins: str = "http://localhost:5173,http://127.0.0.1:5173,http://localhost:5174,http://127.0.0.1:5174"
    live_trading_enabled: bool = False
    default_exchange: str = "hyperliquid"
    default_timeframe: str = "4h"
    default_account_size: float = 10_000
    default_risk_per_trade: float = Field(default=1.0, ge=0.01, le=20)
    default_min_rr: float = Field(default=2.0, ge=0.1)
    default_max_open_trades: int = Field(default=3, ge=1)
    min_perp_volume_24h: float = Field(default=100_000, ge=0)
    hyperliquid_candle_fetch_attempts: int = Field(default=3, ge=1, le=5)
    hyperliquid_candle_fetch_backoff_seconds: float = Field(default=0.7, ge=0)
    hyperliquid_candle_request_delay_seconds: float = Field(default=0.8, ge=0)
    hyperliquid_candle_concurrency: int = Field(default=1, ge=1, le=20)
    api_rate_limit_per_minute: int = Field(default=120, ge=1)
    scanner_rate_limit_per_minute: int = Field(default=20, ge=1)
    signal_max_age_minutes: int = Field(default=1440, ge=5)
    signal_max_entry_deviation_percent: float = Field(default=1.5, ge=0)
    trade_history_expiry_bars: int = Field(default=12, ge=1)
    trade_history_move_stop_to_entry_after_tp1: bool = False
    execution_autotrade_enabled: bool = False
    execution_signal_webhook_url: str = ""
    execution_webhook_secret: str = ""
    genlayer_enabled: bool = True
    genlayer_mode: str = "mock"
    genlayer_api_base_url: str = ""
    genlayer_api_key: str = ""
    genlayer_validator_services: str = ""
    genlayer_llm_provider: str = ""
    genlayer_intelligent_contract_address: str = ""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


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
