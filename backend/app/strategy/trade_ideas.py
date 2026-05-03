import pandas as pd

from app.models.schemas import AnalysisResponse, LiquiditySweep, RiskSettings, TradeIdea, Zone
from app.strategy.liquidity_sweep import detect_liquidity_sweeps
from app.strategy.market_structure import (
    classify_market,
    higher_timeframe_bias,
    momentum_confirmation,
    range_position,
    volume_confirmation,
)
from app.strategy.support_resistance import average_true_range, find_support_resistance, nearest_range


MIN_SETUP_SCORE = 65


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


def _latest_sweep(sweeps: list[LiquiditySweep], direction: str, confirmed_only: bool = True) -> LiquiditySweep | None:
    candidates = [sweep for sweep in sweeps if sweep.direction == direction]
    if confirmed_only:
        candidates = [sweep for sweep in candidates if sweep.confirmation_status == "confirmed"]
    return candidates[-1] if candidates else None


def _grade(score: float) -> str:
    if score >= 80:
        return "A+ Setup"
    if score >= 65:
        return "Valid Setup"
    if score >= 50:
        return "Weak Setup"
    return "No Trade"


def _alignment_points(direction: str, bias: str) -> tuple[int, str]:
    if bias == "HTF_NEUTRAL":
        return 8, "neutral"
    if (direction == "Long" and bias == "HTF_BULLISH") or (direction == "Short" and bias == "HTF_BEARISH"):
        return 15, "aligned"
    return 2, "against"


def _score_setup(
    *,
    regime: str,
    direction: str,
    zone: Zone,
    sweep: LiquiditySweep | None,
    htf_bias: str,
    rr: float,
    vol_ok: bool,
    mom_ok: bool,
    distance_from_mid: float,
) -> tuple[float, dict[str, int | str]]:
    regime_points = {
        "RANGE_BOUND": 18,
        "TRENDING_UP": 18 if direction == "Long" else 6,
        "TRENDING_DOWN": 18 if direction == "Short" else 6,
        "BREAKOUT": 18 if direction == "Long" else 4,
        "BREAKDOWN": 18 if direction == "Short" else 4,
    }.get(regime, 0)
    zone_points = min(20, int((zone.strength_score or zone.strength * 100) / 5))
    sweep_points = 0
    if sweep:
        sweep_points = min(20, int((sweep.sweep_quality_score or sweep.strength * 100) / 5))
    elif regime in {"BREAKOUT", "BREAKDOWN", "TRENDING_UP", "TRENDING_DOWN"}:
        sweep_points = 8

    htf_points, alignment = _alignment_points(direction, htf_bias)
    rr_points = min(10, int(max(0, rr) / 3 * 10))
    momentum_points = min(10, (5 if vol_ok else 0) + (5 if mom_ok else 0))
    distance_points = min(5, int(distance_from_mid * 10))

    total = regime_points + zone_points + sweep_points + htf_points + rr_points + momentum_points + distance_points
    return float(min(100, total)), {
        "regime": regime_points,
        "zone": zone_points,
        "sweep": sweep_points,
        "htf": htf_points,
        "alignment": alignment,
        "rr": rr_points,
        "momentum": momentum_points,
        "distance": distance_points,
    }


def _reason(regime: str, direction: str, sweep: LiquiditySweep | None, htf_bias: str, alignment: str) -> str:
    pieces = []
    if regime == "RANGE_BOUND":
        pieces.append("Price is trading at a clean range extreme instead of the middle.")
    elif regime in {"TRENDING_UP", "TRENDING_DOWN"}:
        pieces.append("Market structure favors trend-continuation pullbacks.")
    elif regime == "BREAKOUT":
        pieces.append("Price is holding above resistance with continuation confirmation.")
    elif regime == "BREAKDOWN":
        pieces.append("Price is holding below support with continuation confirmation.")

    if sweep:
        pieces.append(
            f"{direction} idea has a confirmed liquidity sweep/reclaim with quality score {sweep.sweep_quality_score or round(sweep.strength * 100, 1)}."
        )
    if alignment == "aligned":
        pieces.append(f"Higher timeframe bias is aligned ({htf_bias}).")
    elif alignment == "against":
        pieces.append(f"Higher timeframe bias conflicts with this setup ({htf_bias}), so confidence is reduced.")
    return " ".join(pieces)


def _build_idea(
    *,
    symbol: str,
    timeframe: str,
    exchange: str,
    direction: str,
    regime: str,
    htf_bias: str,
    entry_low: float,
    entry_high: float,
    stop: float,
    tp1: float,
    tp2: float,
    zone: Zone,
    sweep: LiquiditySweep | None,
    settings: RiskSettings,
    vol_ok: bool,
    mom_ok: bool,
    distance_from_mid: float,
) -> TradeIdea | None:
    entry = (entry_low + entry_high) / 2
    rr = _rr(entry, stop, tp2, direction)
    if rr < settings.min_rr:
        return None

    score, parts = _score_setup(
        regime=regime,
        direction=direction,
        zone=zone,
        sweep=sweep,
        htf_bias=htf_bias,
        rr=rr,
        vol_ok=vol_ok,
        mom_ok=mom_ok,
        distance_from_mid=distance_from_mid,
    )
    if score < MIN_SETUP_SCORE:
        return None

    size, risk_amount = _risk_size(settings, entry, stop)
    invalid_timeframe = timeframe.upper()
    invalid = f"Invalid if a {invalid_timeframe} candle closes {'below' if direction == 'Long' else 'above'} {round(stop, 6)}."
    reason = _reason(regime, direction, sweep, htf_bias, str(parts["alignment"]))

    return TradeIdea(
        symbol=symbol,
        timeframe=timeframe,
        exchange=exchange,
        direction=direction,
        market_regime=regime,
        higher_timeframe_bias=htf_bias,
        setup_grade=_grade(score),
        setup_score=round(score, 1),
        entry_zone=(round(entry_low, 6), round(entry_high, 6)),
        stop_loss=round(stop, 6),
        take_profit_1=round(tp1, 6),
        take_profit_2=round(tp2, 6),
        risk_reward_ratio=rr,
        reason=reason,
        confidence_score=round(score, 1),
        invalid_condition=invalid,
        warning="Not financial advice. Manage risk.",
        rank_score=round(score * 1.4 + rr * 6 + (sweep.sweep_quality_score or 0 if sweep else 0) * 0.15 + (zone.strength_score or 0) * 0.1, 2),
        position_size_units=size,
        risk_amount=risk_amount,
    )


def build_trade_ideas(
    symbol: str,
    timeframe: str,
    exchange: str,
    df: pd.DataFrame,
    support: Zone | None,
    resistance: Zone | None,
    sweeps: list[LiquiditySweep],
    settings: RiskSettings,
    regime: str,
    htf_bias: str,
) -> tuple[list[TradeIdea], str | None]:
    if support is None or resistance is None:
        return [], "NO TRADE: not enough clean support/resistance structure."

    price = float(df["close"].iloc[-1])
    atr = average_true_range(df)
    range_width = resistance.lower - support.upper
    if range_width <= atr * 1.4:
        return [], "NO TRADE: range is too compressed and choppy."

    position = range_position(price, support, resistance)
    vol_ok = volume_confirmation(df)
    mom_ok = momentum_confirmation(df)
    bullish_sweep = _latest_sweep(sweeps, "bullish")
    bearish_sweep = _latest_sweep(sweeps, "bearish")
    unconfirmed = _latest_sweep(sweeps, "bullish", False) or _latest_sweep(sweeps, "bearish", False)

    if regime in {"CHOP", "NO_TRADE"}:
        if position is not None and 0.25 < position < 0.75:
            return [], "NO TRADE: price is currently mid-range."
        if unconfirmed and unconfirmed.confirmation_status != "confirmed":
            return [], "Unconfirmed sweep — no trade yet."
        return [], "NO TRADE: market is choppy or setup quality is unclear."

    ideas: list[TradeIdea] = []
    distance_from_mid = abs((position if position is not None else 0.5) - 0.5)
    stop_buffer = max(atr * 0.65, range_width * 0.035)

    def append(idea: TradeIdea | None) -> None:
        if idea is not None:
            ideas.append(idea)

    if regime == "RANGE_BOUND":
        if position is not None and position <= 0.25:
            sweep_low = min([float(df.loc[df["timestamp"] == bullish_sweep.candle_time, "low"].iloc[0])] if bullish_sweep is not None else [support.lower])
            append(
                _build_idea(
                    symbol=symbol,
                    timeframe=timeframe,
                    exchange=exchange,
                    direction="Long",
                    regime=regime,
                    htf_bias=htf_bias,
                    entry_low=support.lower,
                    entry_high=min(price, support.upper + atr * 0.35),
                    stop=min(support.lower, sweep_low) - stop_buffer,
                    tp1=support.upper + range_width * 0.5,
                    tp2=resistance.lower,
                    zone=support,
                    sweep=bullish_sweep,
                    settings=settings,
                    vol_ok=vol_ok,
                    mom_ok=mom_ok,
                    distance_from_mid=distance_from_mid,
                )
            )
        elif position is not None and position >= 0.75:
            sweep_high = max([float(df.loc[df["timestamp"] == bearish_sweep.candle_time, "high"].iloc[0])] if bearish_sweep is not None else [resistance.upper])
            append(
                _build_idea(
                    symbol=symbol,
                    timeframe=timeframe,
                    exchange=exchange,
                    direction="Short",
                    regime=regime,
                    htf_bias=htf_bias,
                    entry_low=max(price, resistance.lower - atr * 0.35),
                    entry_high=resistance.upper,
                    stop=max(resistance.upper, sweep_high) + stop_buffer,
                    tp1=resistance.lower - range_width * 0.5,
                    tp2=support.upper,
                    zone=resistance,
                    sweep=bearish_sweep,
                    settings=settings,
                    vol_ok=vol_ok,
                    mom_ok=mom_ok,
                    distance_from_mid=distance_from_mid,
                )
            )

    if regime == "TRENDING_UP" and position is not None and position <= 0.4:
        append(
            _build_idea(
                symbol=symbol,
                timeframe=timeframe,
                exchange=exchange,
                direction="Long",
                regime=regime,
                htf_bias=htf_bias,
                entry_low=max(support.lower, price - atr * 0.25),
                entry_high=min(price, support.upper + atr * 0.45),
                stop=support.lower - stop_buffer,
                tp1=price + atr * 2.0,
                tp2=price + atr * 3.8,
                zone=support,
                sweep=bullish_sweep,
                settings=settings,
                vol_ok=vol_ok,
                mom_ok=mom_ok,
                distance_from_mid=distance_from_mid,
            )
        )

    if regime == "TRENDING_DOWN" and position is not None and position >= 0.6:
        append(
            _build_idea(
                symbol=symbol,
                timeframe=timeframe,
                exchange=exchange,
                direction="Short",
                regime=regime,
                htf_bias=htf_bias,
                entry_low=max(price, resistance.lower - atr * 0.45),
                entry_high=min(resistance.upper, price + atr * 0.25),
                stop=resistance.upper + stop_buffer,
                tp1=price - atr * 2.0,
                tp2=price - atr * 3.8,
                zone=resistance,
                sweep=bearish_sweep,
                settings=settings,
                vol_ok=vol_ok,
                mom_ok=mom_ok,
                distance_from_mid=distance_from_mid,
            )
        )

    if regime == "BREAKOUT" and (vol_ok or mom_ok):
        append(
            _build_idea(
                symbol=symbol,
                timeframe=timeframe,
                exchange=exchange,
                direction="Long",
                regime=regime,
                htf_bias=htf_bias,
                entry_low=max(resistance.upper, price - atr * 0.4),
                entry_high=price,
                stop=resistance.lower - stop_buffer,
                tp1=price + atr * 2.0,
                tp2=price + atr * 3.8,
                zone=resistance,
                sweep=None,
                settings=settings,
                vol_ok=vol_ok,
                mom_ok=mom_ok,
                distance_from_mid=0.5,
            )
        )

    if regime == "BREAKDOWN" and (vol_ok or mom_ok):
        append(
            _build_idea(
                symbol=symbol,
                timeframe=timeframe,
                exchange=exchange,
                direction="Short",
                regime=regime,
                htf_bias=htf_bias,
                entry_low=price,
                entry_high=min(support.lower, price + atr * 0.4),
                stop=support.upper + stop_buffer,
                tp1=price - atr * 2.0,
                tp2=price - atr * 3.8,
                zone=support,
                sweep=None,
                settings=settings,
                vol_ok=vol_ok,
                mom_ok=mom_ok,
                distance_from_mid=0.5,
            )
        )

    if not ideas:
        if unconfirmed and unconfirmed.confirmation_status != "confirmed":
            return [], "Unconfirmed sweep — no trade yet."
        return [], "NO TRADE: setup score is below 65 or risk/reward is not good enough."
    return sorted(ideas, key=lambda idea: idea.rank_score, reverse=True), None


def analyze_dataframe(
    symbol: str,
    timeframe: str,
    exchange: str,
    df: pd.DataFrame,
    settings: RiskSettings,
    htf_dfs: list[pd.DataFrame] | None = None,
) -> AnalysisResponse:
    supports, resistances = find_support_resistance(df)
    support, resistance = nearest_range(float(df["close"].iloc[-1]), supports, resistances)
    sweeps = detect_liquidity_sweeps(df, supports, resistances)
    regime = classify_market(df, support, resistance)
    htf_bias = higher_timeframe_bias(htf_dfs)
    ideas, no_trade_reason = build_trade_ideas(
        symbol,
        timeframe,
        exchange,
        df,
        support,
        resistance,
        sweeps,
        settings,
        regime,
        htf_bias,
    )
    warning = no_trade_reason if not ideas else None

    return AnalysisResponse(
        symbol=symbol,
        timeframe=timeframe,
        exchange=exchange,
        current_price=round(float(df["close"].iloc[-1]), 6),
        market_condition=regime,
        support_zones=supports,
        resistance_zones=resistances,
        liquidity_sweeps=sweeps,
        trade_ideas=ideas,
        warning=warning,
        higher_timeframe_bias=htf_bias,
        no_trade_reason=no_trade_reason,
    )
