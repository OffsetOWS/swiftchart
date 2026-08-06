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
    cmc_api_key: str = ""
    cmc_api_base_url: str = "https://pro-api.coinmarketcap.com"
    cmc_cache_ttl_seconds: int = Field(default=300, ge=30, le=3600)
    cmc_request_timeout_seconds: float = Field(default=8.0, ge=1, le=30)
    oanda_api_key: str = ""
    oanda_account_id: str = ""
    oanda_env: str = "practice"
    oanda_base_url: str = "https://api-fxpractice.oanda.com"
    oanda_request_timeout_seconds: float = Field(default=12.0, ge=1, le=30)
    oanda_retry_attempts: int = Field(default=3, ge=1, le=5)
    oanda_retry_backoff_seconds: float = Field(default=0.5, ge=0, le=10)
    twelve_data_api_key: str = ""
    internal_api_secret: str = ""
    forex_scanner_enabled: bool = True
    forex_enabled_timeframes: str = "15M,1H,4H,1D"
    forex_candle_close_delay_seconds: int = Field(default=15, ge=0, le=300)
    forex_bootstrap_candle_limit: int = Field(default=500, ge=60, le=5000)
    forex_incremental_candle_limit: int = Field(default=8, ge=2, le=100)
    forex_data_lock_timeout_seconds: int = Field(default=20, ge=1, le=120)
    forex_data_lock_stale_seconds: int = Field(default=90, ge=10, le=600)
    forex_risk_percentage_per_trade: float = Field(default=1.0, gt=0, le=5)
    forex_max_monetary_risk_per_trade: float = Field(default=1000.0, gt=0)
    forex_min_stop_pips: float = Field(default=1.0, gt=0)
    forex_max_stop_pips: float = Field(default=300.0, gt=0)
    forex_min_position_size: float = Field(default=1.0, gt=0)
    forex_max_position_size: float = Field(default=10_000_000.0, gt=0)
    forex_max_total_open_risk_percentage: float = Field(default=3.0, gt=0, le=20)
    forex_max_correlated_exposure: int = Field(default=2, ge=1, le=20)
    forex_scan_interval_seconds: int = Field(default=900, ge=60)
    forex_scan_15m_interval_seconds: int = Field(default=900, ge=60)
    forex_scan_1h_interval_seconds: int = Field(default=3600, ge=60)
    forex_scan_4h_interval_seconds: int = Field(default=14400, ge=60)
    forex_scan_1d_interval_seconds: int = Field(default=86400, ge=60)
    forex_lifecycle_interval_seconds: int = Field(default=60, ge=30)
    forex_worker_startup_delay_seconds: int = Field(default=20, ge=0)
    forex_dxy_context_enabled: bool = True
    forex_oil_context_enabled: bool = True
    forex_cross_market_max_positive_adjustment: float = Field(default=10.0, ge=0, le=20)
    forex_cross_market_max_negative_adjustment: float = Field(default=-10.0, ge=-20, le=0)
    forex_cross_market_stale_multiplier: float = Field(default=2.5, ge=1, le=10)
    forex_liquidity_fvg_limit_enabled: bool = False
    forex_liquidity_fvg_limit_shadow_mode: bool = True
    forex_liquidity_fvg_auto_execution_enabled: bool = False
    forex_fvg_entry_mode: str = "FVG_MIDPOINT"
    forex_fvg_min_gap_atr: float = Field(default=0.10, gt=0, le=2)
    forex_fvg_min_gap_pips: float = Field(default=2.0, gt=0)
    forex_fvg_displacement_atr: float = Field(default=1.0, gt=0, le=5)
    forex_fvg_sweep_lookback: int = Field(default=20, ge=8, le=100)
    forex_fvg_expiry_1h_candles: int = Field(default=8, ge=1, le=100)
    forex_fvg_expiry_4h_candles: int = Field(default=6, ge=1, le=100)
    forex_fvg_expiry_1d_candles: int = Field(default=3, ge=1, le=100)

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
