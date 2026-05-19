from functools import lru_cache

from pydantic import Field
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ExecutionSettings(BaseSettings):
    execution_database_url: str = "sqlite:///./execution_bot.db"
    execution_webhook_secret: str = ""
    execution_mode: str = "paper"
    execution_live_confirm: bool = False
    live_trading: bool = False
    auto_execute: bool = False
    execution_exchange: str = "mock"
    execution_quote_asset: str = "USDT"
    execution_telegram_bot_token: str = ""
    execution_telegram_admin_id: int | None = None
    execution_telegram_polling_enabled: bool = True
    telegram_bot_token: str = ""
    telegram_admin_id: int | None = None
    telegram_polling_enabled: bool = True

    starting_balance: float = Field(default=100, gt=0)
    target_balance: float = Field(default=1000, gt=0)
    base_risk_percent: float = Field(default=2, gt=0)
    max_risk_percent: float = Field(default=5, gt=0)
    max_daily_loss_percent: float = Field(default=8, gt=0)
    max_weekly_loss_percent: float = Field(default=15, gt=0)
    max_open_trades: int = Field(default=2, ge=1)
    max_leverage: float = Field(default=5, ge=1)
    max_consecutive_losses: int = Field(default=3, ge=1)
    min_confidence_to_trade: float = Field(default=75, ge=0, le=100)
    max_exposure_per_coin_percent: float = Field(default=45, gt=0, le=100)
    min_order_notional: float = Field(default=10, ge=0)
    cooldown_after_loss_minutes: int = Field(default=30, ge=0)
    signal_ttl_seconds: int = Field(default=900, ge=30)
    duplicate_window_seconds: int = Field(default=3600, ge=60)
    min_atr_percent: float = Field(default=0.08, gt=0)
    max_atr_percent: float = Field(default=8, gt=0)
    max_spread_percent: float = Field(default=0.25, gt=0)
    min_volume_ratio: float = Field(default=0.55, gt=0)

    hyperliquid_base_url: str = "https://api.hyperliquid.xyz"
    hyperliquid_wallet_address: str = ""
    hyperliquid_private_key: str = ""
    hyperliquid_api_wallet_address: str = ""
    hyperliquid_api_key: str = ""
    hyperliquid_api_secret: str = ""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @field_validator("telegram_admin_id", "execution_telegram_admin_id", mode="before")
    @classmethod
    def blank_admin_id_disables_telegram(cls, value):
        if value == "":
            return None
        return value

    @property
    def live_enabled(self) -> bool:
        return (self.execution_mode.lower() == "live" and self.execution_live_confirm) or self.live_trading

    @property
    def effective_telegram_bot_token(self) -> str:
        return self.execution_telegram_bot_token or self.telegram_bot_token

    @property
    def effective_telegram_admin_id(self) -> int | None:
        return self.execution_telegram_admin_id or self.telegram_admin_id

    @property
    def effective_telegram_polling_enabled(self) -> bool:
        return self.execution_telegram_polling_enabled and self.telegram_polling_enabled

    @property
    def effective_hyperliquid_wallet_address(self) -> str:
        return self.hyperliquid_wallet_address or self.hyperliquid_api_wallet_address

    @property
    def effective_hyperliquid_signing_secret(self) -> str:
        for value in (self.hyperliquid_api_secret, self.hyperliquid_private_key, self.hyperliquid_api_key):
            if self._looks_like_private_key(value):
                return value
        return ""

    @staticmethod
    def _looks_like_private_key(value: str) -> bool:
        cleaned = value.strip()
        if cleaned.startswith("0x"):
            cleaned = cleaned[2:]
        if len(cleaned) != 64:
            return False
        try:
            int(cleaned, 16)
        except ValueError:
            return False
        return True


@lru_cache
def get_execution_settings() -> ExecutionSettings:
    return ExecutionSettings()
