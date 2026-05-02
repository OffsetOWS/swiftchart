import pandas as pd

from app.models.schemas import AnalysisResponse, LiquiditySweep, RiskSettings, TradeIdea, Zone
from app.strategy.liquidity_sweep import detect_liquidity_sweeps
from app.strategy.market_structure import classify_market, momentum_confirmation, volume_confirmation
from app.strategy.support_resistance import average_true_range, find_support_resistance, nearest_range


def _rr(entry: float, stop: float, target: float, direction: str) -> float:
    risk = abs(entry - stop)
    reward = target - entry if direction == "Long" else entry - target
    if risk <= 0:
        return 0
    return round(max(0, reward / risk), 2)


def _risk_size(settings: RiskSettings, entry: float, stop: float) -> tuple[float, float]:
    risk_amount = settings.account_size * settings.risk_per_trade_pct / 100
    risk_per_unit = abs(entry - stop)
    if risk_per_unit <= 0:
        return 0, risk_amount
    return round(risk_amount / risk_per_unit, 6), round(risk_amount, 2)


def _latest_sweep(sweeps: list[LiquiditySweep], direction: str) -> LiquiditySweep | None:
    candidates = [sweep for sweep in sweeps if sweep.direction == direction]
    return candidates[-1] if candidates else None


def build_trade_ideas(
    symbol: str,
    timeframe: str,
    exchange: str,
    df: pd.DataFrame,
    support: Zone | None,
    resistance: Zone | None,
    sweeps: list[LiquiditySweep],
    settings: RiskSettings,
) -> list[TradeIdea]:
    if support is None or resistance is None:
        return []

    price = float(df["close"].iloc[-1])
    atr = average_true_range(df)
    range_width = resistance.lower - support.upper
    if range_width <= atr:
        return []

    range_position = (price - support.upper) / range_width
    vol_ok = volume_confirmation(df)
    mom_ok = momentum_confirmation(df)
    ideas: list[TradeIdea] = []

    near_support = price <= support.upper + range_width * 0.22
    near_resistance = price >= resistance.lower - range_width * 0.22
    bullish_sweep = _latest_sweep(sweeps, "bullish")
    bearish_sweep = _latest_sweep(sweeps, "bearish")

    def add_idea(direction: str, entry_low: float, entry_high: float, stop: float, tp1: float, tp2: float, reason: str, confidence: float) -> None:
        entry = (entry_low + entry_high) / 2
        rr = _rr(entry, stop, tp2, direction)
        if rr < settings.min_rr:
            return
        size, risk_amount = _risk_size(settings, entry, stop)
        ideas.append(
            TradeIdea(
                symbol=symbol,
                timeframe=timeframe,
                exchange=exchange,
                direction=direction,
                entry_zone=(round(entry_low, 6), round(entry_high, 6)),
                stop_loss=round(stop, 6),
                take_profit_1=round(tp1, 6),
                take_profit_2=round(tp2, 6),
                risk_reward_ratio=rr,
                reason=reason,
                confidence_score=round(min(100, confidence), 1),
                invalid_condition="Invalid if price closes beyond stop zone. No trade if price remains mid-range.",
                position_size_units=size,
                risk_amount=risk_amount,
            )
        )

    if near_support or bullish_sweep:
        confidence = 58 + support.strength * 18 + (14 if bullish_sweep else 0) + (6 if vol_ok else 0)
        add_idea(
            "Long",
            support.lower,
            min(price, support.upper + atr * 0.4),
            support.lower - atr * 0.55,
            price + range_width * 0.45,
            resistance.lower,
            "Potential setup near strong range support with reclaim/sweep confirmation." if bullish_sweep else "Potential setup near strong range support.",
            confidence,
        )

    if near_resistance or bearish_sweep:
        confidence = 58 + resistance.strength * 18 + (14 if bearish_sweep else 0) + (6 if vol_ok else 0)
        add_idea(
            "Short",
            max(price, resistance.lower - atr * 0.4),
            resistance.upper,
            resistance.upper + atr * 0.55,
            price - range_width * 0.45,
            support.upper,
            "Potential setup after resistance sweep and rejection." if bearish_sweep else "Potential setup near strong range resistance.",
            confidence,
        )

    if resistance and price > resistance.upper and (vol_ok or mom_ok):
        entry_low = max(resistance.upper, price - atr * 0.35)
        add_idea(
            "Long",
            entry_low,
            price,
            resistance.lower - atr * 0.25,
            price + atr * 1.8,
            price + atr * 3.2,
            "Potential breakout long after close above resistance with volume or momentum confirmation.",
            64 + resistance.strength * 14 + (8 if vol_ok else 0) + (8 if mom_ok else 0),
        )

    if support and price < support.lower and (vol_ok or mom_ok):
        entry_high = min(support.lower, price + atr * 0.35)
        add_idea(
            "Short",
            price,
            entry_high,
            support.upper + atr * 0.25,
            price - atr * 1.8,
            price - atr * 3.2,
            "Potential breakdown short after close below support with volume or momentum confirmation.",
            64 + support.strength * 14 + (8 if vol_ok else 0) + (8 if mom_ok else 0),
        )

    for idea in ideas:
        distance_bonus = max(0, 20 - abs(range_position - (0 if idea.direction == "Long" else 1)) * 20)
        sweep_bonus = 10 if (idea.direction == "Long" and bullish_sweep) or (idea.direction == "Short" and bearish_sweep) else 0
        volume_bonus = 8 if vol_ok else 0
        idea.rank_score = round(
            support.strength * 12
            + resistance.strength * 12
            + distance_bonus
            + sweep_bonus
            + min(18, idea.risk_reward_ratio * 3)
            + volume_bonus
            + idea.confidence_score * 0.25,
            2,
        )

    return sorted(ideas, key=lambda idea: idea.rank_score, reverse=True)


def analyze_dataframe(symbol: str, timeframe: str, exchange: str, df: pd.DataFrame, settings: RiskSettings) -> AnalysisResponse:
    supports, resistances = find_support_resistance(df)
    support, resistance = nearest_range(float(df["close"].iloc[-1]), supports, resistances)
    sweeps = detect_liquidity_sweeps(df, supports, resistances)
    condition = classify_market(df, support, resistance)
    ideas = build_trade_ideas(symbol, timeframe, exchange, df, support, resistance, sweeps, settings)
    warning = None
    if condition == "No-trade zone" or not ideas:
        warning = "No trade zone. Price is mid-range or setup is unclear."

    return AnalysisResponse(
        symbol=symbol,
        timeframe=timeframe,
        exchange=exchange,
        current_price=round(float(df["close"].iloc[-1]), 6),
        market_condition=condition,
        support_zones=supports,
        resistance_zones=resistances,
        liquidity_sweeps=sweeps,
        trade_ideas=ideas,
        warning=warning,
    )
