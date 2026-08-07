from __future__ import annotations

from dataclasses import dataclass

from app.config import get_settings


ALL_FOREX_TIMEFRAMES = ("15M", "1H", "4H", "1D")
# Compatibility catalog for historical records and provider-level tests.
SUPPORTED_FOREX_TIMEFRAMES = ALL_FOREX_TIMEFRAMES
PROVIDER_TIMEFRAMES = {value: value.lower() for value in ALL_FOREX_TIMEFRAMES}
TIMEFRAME_EXPIRY_HOURS = {"15M": 3, "1H": 12, "4H": 48, "1D": 240}
DEFAULT_FOREX_TIMEFRAMES = {
    "execution": "1h",
    "setup": "4h",
    "bias": "1d",
}

STRATEGY_FAMILY = "swiftchart_fx_structure"
STRATEGY_VERSION = "3.0"


def enabled_forex_timeframes() -> tuple[str, ...]:
    configured = get_settings().forex_enabled_timeframes
    values = tuple(
        alias
        for item in configured.split(",")
        if (alias := _normalize_timeframe_value(item)) in ALL_FOREX_TIMEFRAMES
    )
    return values or ALL_FOREX_TIMEFRAMES


def _normalize_timeframe_value(value: str) -> str:
    normalized = str(value or "").strip().upper()
    aliases = {"15MIN": "15M", "60M": "1H", "D": "1D", "DAILY": "1D"}
    return aliases.get(normalized, normalized)


def normalize_forex_timeframe(value: str, *, require_enabled: bool = True) -> str:
    normalized = _normalize_timeframe_value(value)
    supported = enabled_forex_timeframes() if require_enabled else ALL_FOREX_TIMEFRAMES
    if normalized not in supported:
        raise ValueError(f"Unsupported Forex timeframe: {value}")
    return normalized


@dataclass(frozen=True)
class ForexPairConfig:
    pair: str
    provider_symbol: str
    pip_size: float
    relevant_sessions: tuple[str, ...]
    max_spread_pips: float
    min_atr_pips_1h: float
    max_atr_pips_1h: float


SUPPORTED_FOREX_PAIRS: dict[str, ForexPairConfig] = {
    "EURUSD": ForexPairConfig("EURUSD", "EUR/USD", 0.0001, ("London", "New York", "London-New York overlap"), 1.5, 4, 45),
    "GBPUSD": ForexPairConfig("GBPUSD", "GBP/USD", 0.0001, ("London", "New York", "London-New York overlap"), 2.0, 5, 60),
    "USDJPY": ForexPairConfig("USDJPY", "USD/JPY", 0.01, ("Tokyo", "London", "New York"), 2.0, 5, 65),
    "AUDUSD": ForexPairConfig("AUDUSD", "AUD/USD", 0.0001, ("Tokyo", "London-New York overlap"), 2.0, 4, 45),
    "USDCAD": ForexPairConfig("USDCAD", "USD/CAD", 0.0001, ("New York", "London-New York overlap"), 2.0, 4, 55),
    "USDCHF": ForexPairConfig("USDCHF", "USD/CHF", 0.0001, ("London", "New York"), 2.0, 4, 50),
    "NZDUSD": ForexPairConfig("NZDUSD", "NZD/USD", 0.0001, ("Tokyo", "London-New York overlap"), 2.5, 3, 42),
    "EURGBP": ForexPairConfig("EURGBP", "EUR/GBP", 0.0001, ("London",), 1.8, 3, 40),
    "EURJPY": ForexPairConfig("EURJPY", "EUR/JPY", 0.01, ("Tokyo", "London"), 2.5, 5, 70),
    "GBPJPY": ForexPairConfig("GBPJPY", "GBP/JPY", 0.01, ("Tokyo", "London"), 3.5, 7, 90),
    "XAUUSD": ForexPairConfig("XAUUSD", "XAU/USD", 0.1, ("London", "New York", "London-New York overlap"), 35.0, 10, 350),
    "CADJPY": ForexPairConfig("CADJPY", "CAD/JPY", 0.01, ("Tokyo", "London", "New York"), 3.0, 5, 75),
    "EURCAD": ForexPairConfig("EURCAD", "EUR/CAD", 0.0001, ("London", "New York"), 2.5, 4, 65),
    "GBPCAD": ForexPairConfig("GBPCAD", "GBP/CAD", 0.0001, ("London", "New York"), 3.5, 6, 85),
    "AUDCAD": ForexPairConfig("AUDCAD", "AUD/CAD", 0.0001, ("Tokyo", "London"), 2.5, 4, 55),
    "NZDCAD": ForexPairConfig("NZDCAD", "NZD/CAD", 0.0001, ("Tokyo", "London"), 3.0, 4, 55),
    "CADCHF": ForexPairConfig("CADCHF", "CAD/CHF", 0.0001, ("London", "New York"), 2.5, 4, 55),
}

NEWS_KEYWORDS = (
    "CPI",
    "NFP",
    "FOMC",
    "interest rate decision",
    "central bank speech",
    "GDP",
    "unemployment data",
)
