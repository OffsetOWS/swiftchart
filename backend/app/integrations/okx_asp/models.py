from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.config import SUPPORTED_TIMEFRAMES


class OKXAnalyzeMarketRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=30)
    timeframe: str = Field(min_length=2, max_length=8)

    model_config = ConfigDict(extra="forbid")

    @field_validator("symbol")
    @classmethod
    def validate_symbol(cls, value: str) -> str:
        raw = value.strip().upper()
        if not raw or not re.fullmatch(r"[A-Z0-9/_-]+", raw):
            raise ValueError("Symbol may contain only letters, numbers, '/', '-' or '_'.")
        normalized = re.sub(r"[/_-]", "", raw)
        if not normalized or len(normalized) > 20 or not any(character.isalpha() for character in normalized):
            raise ValueError("Invalid crypto symbol.")
        return normalized

    @field_validator("timeframe")
    @classmethod
    def validate_timeframe(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in SUPPORTED_TIMEFRAMES:
            raise ValueError(f"Unsupported timeframe. Use one of: {', '.join(SUPPORTED_TIMEFRAMES)}")
        return normalized


class OKXEntryRange(BaseModel):
    low: float
    high: float


class OKXAnalyzeMarketResponse(BaseModel):
    symbol: str
    timeframe: str
    status: Literal["TRADE", "NO_TRADE"]
    direction: Literal["LONG", "SHORT"] | None
    score: float | None
    grade: str
    entry: OKXEntryRange | None
    stop_loss: float | None = Field(serialization_alias="stopLoss")
    take_profit_1: float | None = Field(serialization_alias="takeProfit1")
    take_profit_2: float | None = Field(serialization_alias="takeProfit2")
    risk_reward: float | None = Field(serialization_alias="riskReward")
    market_bias: Literal["BULLISH", "BEARISH", "NEUTRAL"] = Field(serialization_alias="marketBias")
    reasons: list[str]

    model_config = ConfigDict(populate_by_name=True)
