from __future__ import annotations

from dataclasses import dataclass


SUPPORTED_FOREX_TIMEFRAMES = ("15M", "1H", "4H", "1D")
PROVIDER_TIMEFRAMES = {value: value.lower() for value in SUPPORTED_FOREX_TIMEFRAMES}
TIMEFRAME_EXPIRY_HOURS = {"15M": 3, "1H": 12, "4H": 48, "1D": 240}
DEFAULT_FOREX_TIMEFRAMES = {
    "execution": "15m",
    "setup": "1h",
    "bias": "4h",
}

STRATEGY_FAMILY = "swiftchart_fx_structure"
STRATEGY_VERSION = "3.0"


def normalize_forex_timeframe(value: str) -> str:
    normalized = str(value or "").strip().upper()
    aliases = {"15MIN": "15M", "60M": "1H", "D": "1D", "DAILY": "1D"}
    normalized = aliases.get(normalized, normalized)
    if normalized not in SUPPORTED_FOREX_TIMEFRAMES:
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
    "XAUUSD": ForexPairConfig("XAUUSD", "XAU/USD", 0.1, ("London", "New York", "London-New York overlap"), 35.0, 10, 350),
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
