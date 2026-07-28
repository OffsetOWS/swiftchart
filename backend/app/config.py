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
    frontend_origins: str = "http://localhost:5173,http://127.0.0.1:5173,http://localhost:5174,http://127.0.0.1:5174,http://localhost:5175,http://127.0.0.1:5175"
    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_service_role_key: str = ""
    live_trading_enabled: bool = False
    default_exchange: str = "hyperliquid"
    default_timeframe: str = "4h"
    crypto_background_scanner_enabled: bool = False
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
    okx_asp_api_key: str = ""
    okx_asp_rate_limit_per_minute: int = Field(default=30, ge=1)
    okx_asp_public_rate_limit_per_minute: int = Field(default=10, ge=1)
    okx_asp_analysis_timeout_seconds: float = Field(default=20.0, ge=1, le=25)
    okx_asp_supplemental_data_timeout_seconds: float = Field(default=6.0, ge=1, le=15)
    okx_x402_enabled: bool = False
    okx_x402_api_key: str = ""
    okx_x402_secret_key: str = ""
    okx_x402_passphrase: str = ""
    okx_x402_pay_to_address: str = ""
    okx_x402_price_usd: str = "0.01"
    okx_x402_network: str = "eip155:196"
    okx_x402_resource_url: str = "https://swiftchart.vercel.app/api/asp/okx/public/analyze-market"
    okx_x402_facilitator_base_url: str = "https://web3.okx.com"
    okx_x402_facilitator_timeout_seconds: float = Field(default=10.0, ge=1, le=30)
    signal_max_age_minutes: int = Field(default=1440, ge=5)
    signal_max_entry_deviation_percent: float = Field(default=1.5, ge=0)
    trade_history_expiry_bars: int = Field(default=12, ge=1)
    trade_history_move_stop_to_entry_after_tp1: bool = False
    execution_autotrade_enabled: bool = False
    execution_signal_webhook_url: str = ""
    execution_webhook_secret: str = ""
    ea_api_key: str = "dev-ea-key"
    ea_poll_interval_seconds: int = Field(default=5, ge=1, le=300)
    ea_idle_poll_interval_seconds: int = Field(default=20, ge=15, le=30)
    ea_error_retry_interval_seconds: int = Field(default=12, ge=10, le=15)
    ea_active_poll_interval_seconds: int = Field(default=5, ge=1, le=300)
    ea_max_signals_per_poll: int = Field(default=20, ge=1, le=100)
    genlayer_enabled: bool = True
    genlayer_mode: str = "mock"
    genlayer_api_base_url: str = ""
    genlayer_api_key: str = ""
    genlayer_validator_services: str = ""
    genlayer_llm_provider: str = ""
    genlayer_intelligent_contract_address: str = ""
    cmc_api_key: str = ""
    cmc_api_base_url: str = "https://pro-api.coinmarketcap.com"
    cmc_cache_ttl_seconds: int = Field(default=300, ge=30, le=3600)
    cmc_request_timeout_seconds: float = Field(default=8.0, ge=1, le=30)
    twelve_data_api_key: str = ""
    mt5_minimum_lot: float = Field(default=0.01, gt=0)
    mt5_maximum_lot: float = Field(default=5.0, gt=0)
    mt5_maximum_total_lots: float = Field(default=10.0, gt=0)
    mt5_risk_per_trade_percent: float = Field(default=1.0, gt=0, le=20)
    mt5_maximum_daily_loss_percent: float = Field(default=3.0, gt=0, le=100)
    mt5_maximum_daily_profit_percent: float = Field(default=5.0, gt=0, le=100)
    mt5_maximum_trades_per_day: int = Field(default=3, ge=1)
    mt5_maximum_open_trades: int = Field(default=3, ge=1)
    mt5_maximum_spread_pips: float = Field(default=2.5, gt=0)
    mt5_minimum_confidence: float = Field(default=75.0, ge=0, le=100)
    mt5_one_trade_per_pair: bool = True
    mt5_break_even_trigger_percent: float = Field(default=0.65, ge=0.1, le=1.0)
    mt5_break_even_buffer_pips: float = Field(default=1.0, ge=0)
    mt5_partial_close_percent: float = Field(default=50.0, gt=0, le=100)
    mt5_trailing_distance_pips: float = Field(default=15.0, gt=0)

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
