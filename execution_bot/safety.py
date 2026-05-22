from __future__ import annotations

from datetime import datetime, timezone

from execution_bot.config import ExecutionSettings
from execution_bot.models import ExecutionPlan, MarketSnapshot, SignalIn


TIMEFRAME_SECONDS = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "2h": 7200,
    "4h": 14400,
    "1d": 86400,
}


def spread_percent(snapshot: MarketSnapshot) -> float:
    if snapshot.bid is None or snapshot.ask is None or snapshot.ask <= snapshot.bid:
        return 0.0
    mid = (snapshot.bid + snapshot.ask) / 2
    return ((snapshot.ask - snapshot.bid) / mid) * 100 if mid > 0 else 0.0


def reference_price(snapshot: MarketSnapshot) -> float | None:
    if snapshot.mark_price and snapshot.mark_price > 0:
        return snapshot.mark_price
    if snapshot.bid and snapshot.ask and snapshot.ask > snapshot.bid:
        return (snapshot.bid + snapshot.ask) / 2
    if snapshot.candles:
        return snapshot.candles[-1].close
    return None


def validate_market_prechecks(signal: SignalIn, symbol: str, snapshot: MarketSnapshot, settings: ExecutionSettings) -> list[str]:
    reasons: list[str] = []
    allowed_symbols = settings.allowed_execution_symbols
    if allowed_symbols and symbol.upper() not in allowed_symbols:
        reasons.append(f"{symbol.upper()} is not in EXECUTION_SYMBOL_ALLOWLIST.")

    if snapshot.perp_volume_24h is None or snapshot.perp_volume_24h < settings.min_perp_volume_24h:
        reasons.append("Perp 24h volume is below execution minimum.")

    current_spread = spread_percent(snapshot)
    if current_spread > settings.max_spread_percent:
        reasons.append(f"Spread {current_spread:.3f}% exceeds max {settings.max_spread_percent:.3f}%.")

    price = reference_price(snapshot)
    if price is None or price <= 0:
        reasons.append("Reference price is unavailable.")
    else:
        deviation = abs(signal.entry - price) / price * 100
        if deviation > settings.max_entry_deviation_percent:
            reasons.append(f"Entry deviation {deviation:.3f}% exceeds max {settings.max_entry_deviation_percent:.3f}%.")

    latest_candle = snapshot.candles[-1] if snapshot.candles else None
    if latest_candle is None or latest_candle.timestamp is None:
        reasons.append("Latest market candle timestamp is unavailable.")
    else:
        candle_time = latest_candle.timestamp if latest_candle.timestamp.tzinfo else latest_candle.timestamp.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - candle_time).total_seconds()
        max_age = max(settings.max_signal_candle_age_seconds, TIMEFRAME_SECONDS.get(signal.timeframe, 900) * 2)
        if age > max_age:
            reasons.append(f"Latest market candle is stale ({int(age)}s old).")

    return reasons


def validate_plan_prechecks(plan: ExecutionPlan, settings: ExecutionSettings) -> list[str]:
    reasons: list[str] = []
    if plan.notional_value < settings.min_order_notional:
        reasons.append(f"Order value ${plan.notional_value:.2f} is below minimum ${settings.min_order_notional:.2f}.")
    if plan.risk_percent > settings.max_risk_per_trade_percent:
        reasons.append(f"Risk {plan.risk_percent:.2f}% exceeds max {settings.max_risk_per_trade_percent:.2f}%.")
    return reasons
