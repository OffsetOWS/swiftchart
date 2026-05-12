import logging

import pandas as pd

from app.models.schemas import AnalysisResponse, LiquiditySweep, MarketRegimeSnapshot, RiskSettings, SignalReview, TradeIdea, Zone
from app.strategy.liquidity_sweep import detect_liquidity_sweeps
from app.strategy.market_structure import (
    higher_timeframe_bias,
    momentum_confirmation,
    range_position,
    volume_confirmation,
)
from app.strategy.support_resistance import average_true_range, find_support_resistance, nearest_range
from app.strategy.market_regime import detect_market_regime


MIN_SETUP_SCORE = 65

logger = logging.getLogger(__name__)


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
        "TRANSITION_TO_BULLISH": 16 if direction == "Long" else 3,
        "TRANSITION_TO_BEARISH": 16 if direction == "Short" else 3,
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
    elif regime == "TRANSITION_TO_BEARISH":
        pieces.append("Market structure is transitioning bearish and short confirmation is being tested.")
    elif regime == "TRANSITION_TO_BULLISH":
        pieces.append("Market structure is transitioning bullish and long confirmation is being tested.")

    if sweep:
        pieces.append(
            f"{direction} idea has a confirmed liquidity sweep/reclaim with quality score {sweep.sweep_quality_score or round(sweep.strength * 100, 1)}."
        )
    if alignment == "aligned":
        pieces.append(f"Higher timeframe bias is aligned ({htf_bias}).")
    elif alignment == "against":
        pieces.append(f"Higher timeframe bias conflicts with this setup ({htf_bias}), so confidence is reduced.")
    return " ".join(pieces)


def _reversal_confirmations(
    *,
    direction: str,
    df: pd.DataFrame,
    htf_bias: str,
    sweep: LiquiditySweep | None,
    support: Zone | None,
    resistance: Zone | None,
    vol_ok: bool,
    mom_ok: bool,
    market_regime: MarketRegimeSnapshot,
) -> list[str]:
    confirmations: list[str] = []
    close = df["close"].astype(float)
    price = float(close.iloc[-1])
    ema50 = close.ewm(span=50, adjust=False).mean()
    recent = df.tail(6)
    last = df.iloc[-1]

    if direction == "Short":
        if htf_bias == "HTF_BEARISH":
            confirmations.append("BTC/ETH or higher-timeframe bias is weakening")
        if sweep and sweep.direction == "bearish" and sweep.confirmation_status == "confirmed":
            confirmations.append("failed breakout / liquidity sweep")
        if resistance and float(last["close"]) < resistance.lower and float(last["high"]) >= resistance.lower:
            confirmations.append("volume-backed rejection at resistance" if vol_ok else "rejection at resistance")
        if len(close) >= 50 and price < float(ema50.iloc[-1]):
            confirmations.append("price closed below 50 EMA")
        if support and price < support.lower:
            confirmations.append("price closed below support")
        if len(recent) >= 4 and float(recent["low"].iloc[-1]) < float(recent["low"].iloc[:-1].min()):
            confirmations.append("bearish market structure break")
        if mom_ok and float(close.pct_change(6).iloc[-1]) < 0:
            confirmations.append("bearish momentum confirmation")
        if market_regime.components.get("global_score") is not None and float(market_regime.components["global_score"] or 0) < -20:
            confirmations.append("BTC/ETH also weakening")
    else:
        if htf_bias == "HTF_BULLISH":
            confirmations.append("BTC/ETH or higher-timeframe bias is strengthening")
        if sweep and sweep.direction == "bullish" and sweep.confirmation_status == "confirmed":
            confirmations.append("failed breakdown / liquidity sweep")
        if support and float(last["close"]) > support.upper and float(last["low"]) <= support.upper:
            confirmations.append("volume-backed rejection at support" if vol_ok else "rejection at support")
        if len(close) >= 50 and price > float(ema50.iloc[-1]):
            confirmations.append("price closed above 50 EMA")
        if resistance and price > resistance.upper:
            confirmations.append("price closed above resistance")
        if len(recent) >= 4 and float(recent["high"].iloc[-1]) > float(recent["high"].iloc[:-1].max()):
            confirmations.append("bullish market structure break")
        if mom_ok and float(close.pct_change(6).iloc[-1]) > 0:
            confirmations.append("bullish momentum confirmation")
        if market_regime.components.get("global_score") is not None and float(market_regime.components["global_score"] or 0) > 20:
            confirmations.append("BTC/ETH also strengthening")
    return confirmations[:5]


def _regime_alignment(direction: str, market_regime: MarketRegimeSnapshot) -> str:
    regime_type = market_regime.regime_type
    if regime_type == "RANGE_BOUND":
        return "range-trade"
    bullish_types = {"TRENDING_UP", "BREAKOUT", "TRANSITION_TO_BULLISH"}
    bearish_types = {"TRENDING_DOWN", "BREAKDOWN", "TRANSITION_TO_BEARISH"}
    if (direction == "Long" and regime_type in bullish_types) or (direction == "Short" and regime_type in bearish_types):
        return "with-trend"
    return "counter-trend"


def _regime_adjustment(direction: str, score: float, market_regime: MarketRegimeSnapshot, confirmations: list[str]) -> tuple[float, float, str | None]:
    alignment = _regime_alignment(direction, market_regime)
    bearish_structure_active = bool(market_regime.components.get("bearish_structure_active"))
    structure_reclaimed_bullish = bool(market_regime.components.get("structure_reclaimed_bullish"))
    bullish_structure_active = bool(market_regime.components.get("bullish_structure_active"))
    structure_reclaimed_bearish = bool(market_regime.components.get("structure_reclaimed_bearish"))

    if bearish_structure_active and direction == "Long":
        required_reversal_confirmations = 3
        if not structure_reclaimed_bullish:
            penalty = -55
            return score + penalty, penalty, (
                "Long signal rejected because bearish structure is active: price broke recent support, "
                "LH/LL structure is present, and EMA/momentum confirmation is bearish. "
                "Minor bounces are disabled until price reclaims structure."
            )
        if score < 75 or len(confirmations) < required_reversal_confirmations:
            penalty = -35
            return score + penalty, penalty, (
                f"Long signal rejected because bearish structure is active and reversal quality is not high enough; "
                f"score {score:.0f}, confirmations {len(confirmations)}/{required_reversal_confirmations}."
            )

    if bullish_structure_active and direction == "Short":
        required_reversal_confirmations = 3
        if not structure_reclaimed_bearish:
            penalty = -55
            return score + penalty, penalty, (
                "Short signal rejected because bullish structure is active: price reclaimed recent resistance, "
                "HH/HL structure is present, and EMA/momentum confirmation is bullish."
            )
        if score < 75 or len(confirmations) < required_reversal_confirmations:
            penalty = -35
            return score + penalty, penalty, (
                f"Short signal rejected because bullish structure is active and reversal quality is not high enough; "
                f"score {score:.0f}, confirmations {len(confirmations)}/{required_reversal_confirmations}."
            )

    if market_regime.trade_decision == "NO_TRADE":
        penalty = -40
        return score + penalty, penalty, f"{direction} signal rejected because the market regime decision is NO_TRADE ({market_regime.label})."

    if market_regime.is_transition:
        transition_direction = "Long" if market_regime.regime_type == "TRANSITION_TO_BULLISH" else "Short"
        if direction != transition_direction:
            penalty = -35
            return score + penalty, penalty, (
                f"{direction} signal rejected because the market is in {market_regime.label}; "
                f"only {transition_direction.lower()} setups can be reconsidered after confirmation."
            )
        required_transition_confirmations = 2
        if len(confirmations) < required_transition_confirmations:
            penalty = -25
            return score + penalty, penalty, (
                f"{direction} signal rejected because {market_regime.label} needs "
                f"{required_transition_confirmations} confirmations before trading; found {len(confirmations)}."
            )
        boost = 4
        return min(100.0, score + boost), boost, (
            f"Transition {direction.lower()} allowed with {len(confirmations)} bearish confirmations."
            if direction == "Short"
            else f"Transition {direction.lower()} allowed with {len(confirmations)} bullish confirmations."
        )

    if alignment == "range-trade":
        return score, 0, None
    strength = abs(market_regime.score)
    if alignment == "with-trend":
        boost = 8 if strength >= 60 else 4
        return min(100.0, score + boost), boost, "Signal is with the active market regime."

    required = 3 if strength >= 60 else 2
    penalty = -30 if strength >= 60 else -15
    adjusted = score + penalty
    if len(confirmations) < required:
        direction_text = direction.lower()
        return adjusted, penalty, (
            f"{direction} signal rejected because it is counter-trend in a {market_regime.label} regime "
            f"and only has {len(confirmations)} reversal confirmation{'s' if len(confirmations) != 1 else ''}; {required} required."
        )
    return adjusted, penalty, f"Counter-trend {direction.lower()} allowed with {len(confirmations)} strong reversal confirmations."


def _log_signal_review(
    *,
    symbol: str,
    timeframe: str,
    exchange: str,
    direction: str,
    accepted: bool,
    reason: str,
    market_regime: MarketRegimeSnapshot,
    base_score: float,
    adjusted_score: float,
    confidence_adjustment: float,
) -> None:
    logger.info(
        (
            "Signal %s symbol=%s timeframe=%s exchange=%s direction=%s bias=%s bias_reason=%s "
            "flip_trigger=%s regime=%s base_score=%.1f adjusted_score=%.1f adjustment=%.1f reason=%s"
        ),
        "accepted" if accepted else "rejected",
        symbol,
        timeframe,
        exchange,
        direction,
        market_regime.bias,
        market_regime.bias_reason,
        market_regime.bias_flip_trigger,
        market_regime.regime_type,
        base_score,
        adjusted_score,
        confidence_adjustment,
        reason,
    )


def _log_rejected_short_candidate(
    *,
    symbol: str,
    timeframe: str,
    exchange: str,
    reason: str,
    missing_condition: str,
    market_regime: MarketRegimeSnapshot,
) -> None:
    logger.info(
        (
            "Rejected short setup pair=%s timeframe=%s exchange=%s current_bias=%s "
            "reason_rejected=%s missing_condition=%s bias_reason=%s flip_trigger=%s regime=%s"
        ),
        symbol,
        timeframe,
        exchange,
        market_regime.bias,
        reason,
        missing_condition,
        market_regime.bias_reason,
        market_regime.bias_flip_trigger,
        market_regime.regime_type,
    )


def _short_rejection_review(
    *,
    symbol: str,
    timeframe: str,
    exchange: str,
    reason: str,
    missing_condition: str,
    market_regime: MarketRegimeSnapshot,
) -> SignalReview:
    return SignalReview(
        symbol=symbol,
        timeframe=timeframe,
        exchange=exchange,
        direction="Short",
        accepted=False,
        reason=f"{reason} Missing condition: {missing_condition}.",
        base_score=None,
        adjusted_score=None,
        confidence_adjustment=0,
        regime_score=market_regime.score,
        regime_label=market_regime.label,
        trend_alignment=_regime_alignment("Short", market_regime),
        reversal_confirmations=[],
    )


def _build_idea(
    *,
    symbol: str,
    timeframe: str,
    exchange: str,
    direction: str,
    df: pd.DataFrame,
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
    market_regime_data: MarketRegimeSnapshot,
    support: Zone | None,
    resistance: Zone | None,
) -> tuple[TradeIdea | None, SignalReview | None]:
    entry = (entry_low + entry_high) / 2
    rr = _rr(entry, stop, tp2, direction)
    if rr < settings.min_rr:
        rejected_reason = f"Signal rejected because risk/reward {rr:.2f} is below minimum {settings.min_rr:.2f}."
        _log_signal_review(
            symbol=symbol,
            timeframe=timeframe,
            exchange=exchange,
            direction=direction,
            accepted=False,
            reason=rejected_reason,
            market_regime=market_regime_data,
            base_score=0,
            adjusted_score=0,
            confidence_adjustment=0,
        )
        return None, SignalReview(
            symbol=symbol,
            timeframe=timeframe,
            exchange=exchange,
            direction=direction,
            accepted=False,
            reason=rejected_reason,
            base_score=None,
            adjusted_score=None,
            confidence_adjustment=0,
            regime_score=market_regime_data.score,
            regime_label=market_regime_data.label,
            trend_alignment=_regime_alignment(direction, market_regime_data),
            reversal_confirmations=[],
        )

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
    confirmations = _reversal_confirmations(
        direction=direction,
        df=df,
        htf_bias=htf_bias,
        sweep=sweep,
        support=support,
        resistance=resistance,
        vol_ok=vol_ok,
        mom_ok=mom_ok,
        market_regime=market_regime_data,
    )
    adjusted_score, confidence_adjustment, regime_note = _regime_adjustment(direction, score, market_regime_data, confirmations)
    trend_alignment = _regime_alignment(direction, market_regime_data)
    rejected_reason = regime_note if regime_note and "rejected" in regime_note.lower() else None
    if rejected_reason is None and adjusted_score < MIN_SETUP_SCORE:
        rejected_reason = "Signal rejected because setup score is below 65 after regime adjustment."
    if rejected_reason:
        _log_signal_review(
            symbol=symbol,
            timeframe=timeframe,
            exchange=exchange,
            direction=direction,
            accepted=False,
            reason=rejected_reason,
            market_regime=market_regime_data,
            base_score=score,
            adjusted_score=adjusted_score,
            confidence_adjustment=confidence_adjustment,
        )
        return None, SignalReview(
            symbol=symbol,
            timeframe=timeframe,
            exchange=exchange,
            direction=direction,
            accepted=False,
            reason=rejected_reason,
            base_score=round(score, 1),
            adjusted_score=round(adjusted_score, 1),
            confidence_adjustment=round(confidence_adjustment, 1),
            regime_score=market_regime_data.score,
            regime_label=market_regime_data.label,
            trend_alignment=trend_alignment,
            reversal_confirmations=confirmations,
        )

    size, risk_amount = _risk_size(settings, entry, stop)
    invalid_timeframe = timeframe.upper()
    invalid = f"Invalid if a {invalid_timeframe} candle closes {'below' if direction == 'Long' else 'above'} {round(stop, 6)}."
    reason = _reason(regime, direction, sweep, htf_bias, str(parts["alignment"]))
    if regime_note:
        reason = f"{reason} {regime_note}"
    reason = f"{reason} Market Regime: {market_regime_data.label} ({market_regime_data.score:+.0f}); trade is {trend_alignment}; confidence adjustment {confidence_adjustment:+.0f}."

    idea = TradeIdea(
        symbol=symbol,
        timeframe=timeframe,
        exchange=exchange,
        direction=direction,
        market_regime=regime,
        higher_timeframe_bias=htf_bias,
        setup_grade=_grade(adjusted_score),
        setup_score=round(adjusted_score, 1),
        entry_zone=(round(entry_low, 6), round(entry_high, 6)),
        stop_loss=round(stop, 6),
        take_profit_1=round(tp1, 6),
        take_profit_2=round(tp2, 6),
        risk_reward_ratio=rr,
        reason=reason,
        confidence_score=round(adjusted_score, 1),
        invalid_condition=invalid,
        warning="Not financial advice. Manage risk.",
        rank_score=round(score * 1.4 + rr * 6 + (sweep.sweep_quality_score or 0 if sweep else 0) * 0.15 + (zone.strength_score or 0) * 0.1, 2),
        position_size_units=size,
        risk_amount=risk_amount,
        regime_score=market_regime_data.score,
        regime_label=market_regime_data.label,
        regime_type=market_regime_data.regime_type,
        regime_confidence_score=market_regime_data.confidence_score,
        regime_structure=market_regime_data.structure,
        regime_trade_decision=market_regime_data.trade_decision,
        is_regime_transition=market_regime_data.is_transition,
        regime_bias=market_regime_data.bias,
        regime_updated_at=market_regime_data.updated_at,
        trend_alignment=trend_alignment,
        regime_confidence_adjustment=round(confidence_adjustment, 1),
        reversal_confirmations=confirmations,
        regime_explanation=market_regime_data.explanation,
    )
    review = SignalReview(
        symbol=symbol,
        timeframe=timeframe,
        exchange=exchange,
        direction=direction,
        accepted=True,
        reason="Signal accepted after market-regime bias adjustment.",
        base_score=round(score, 1),
        adjusted_score=round(adjusted_score, 1),
        confidence_adjustment=round(confidence_adjustment, 1),
        regime_score=market_regime_data.score,
        regime_label=market_regime_data.label,
        trend_alignment=trend_alignment,
        reversal_confirmations=confirmations,
    )
    _log_signal_review(
        symbol=symbol,
        timeframe=timeframe,
        exchange=exchange,
        direction=direction,
        accepted=True,
        reason=review.reason,
        market_regime=market_regime_data,
        base_score=score,
        adjusted_score=adjusted_score,
        confidence_adjustment=confidence_adjustment,
    )
    return idea, review


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
    market_regime_data: MarketRegimeSnapshot,
) -> tuple[list[TradeIdea], str | None, list[SignalReview]]:
    if market_regime_data.trade_decision == "WAIT" and market_regime_data.regime_type not in {"TRANSITION_TO_BULLISH", "TRANSITION_TO_BEARISH"}:
        logger.info(
            "No setup generation symbol=%s timeframe=%s exchange=%s bias=%s reason=%s flip_trigger=%s decision=WAIT",
            symbol,
            timeframe,
            exchange,
            market_regime_data.bias,
            market_regime_data.bias_reason,
            market_regime_data.bias_flip_trigger,
        )
        return [], (
            f"WAIT / NO TRADE: {market_regime_data.label} has {market_regime_data.confidence_score:.0f}% confidence. "
            f"{market_regime_data.explanation}"
        ), []
    if market_regime_data.trade_decision == "NO_TRADE":
        logger.info(
            "No setup generation symbol=%s timeframe=%s exchange=%s bias=%s reason=%s flip_trigger=%s decision=NO_TRADE",
            symbol,
            timeframe,
            exchange,
            market_regime_data.bias,
            market_regime_data.bias_reason,
            market_regime_data.bias_flip_trigger,
        )
        return [], (
            f"NO TRADE: regime confidence is {market_regime_data.confidence_score:.0f}% or market is {market_regime_data.label}. "
            f"{market_regime_data.explanation}"
        ), []

    if support is None or resistance is None:
        return [], "NO TRADE: not enough clean support/resistance structure.", []

    price = float(df["close"].iloc[-1])
    atr = average_true_range(df)
    range_width = resistance.lower - support.upper
    if range_width <= atr * 1.4:
        return [], "NO TRADE: range is too compressed and choppy.", []

    position = range_position(price, support, resistance)
    vol_ok = volume_confirmation(df)
    mom_ok = momentum_confirmation(df)
    bullish_sweep = _latest_sweep(sweeps, "bullish")
    bearish_sweep = _latest_sweep(sweeps, "bearish")
    unconfirmed = _latest_sweep(sweeps, "bullish", False) or _latest_sweep(sweeps, "bearish", False)

    if regime in {"CHOP", "NO_TRADE"}:
        if position is not None and 0.25 < position < 0.75:
            return [], "NO TRADE: price is currently mid-range.", []
        if unconfirmed and unconfirmed.confirmation_status != "confirmed":
            return [], "Unconfirmed sweep — no trade yet.", []
        return [], "NO TRADE: market is choppy or setup quality is unclear.", []

    ideas: list[TradeIdea] = []
    signal_reviews: list[SignalReview] = []
    distance_from_mid = abs((position if position is not None else 0.5) - 0.5)
    stop_buffer = max(atr * 0.65, range_width * 0.035)

    def append(result: tuple[TradeIdea | None, SignalReview | None]) -> None:
        idea, review = result
        if review is not None:
            signal_reviews.append(review)
        if idea is not None:
            ideas.append(idea)

    def reject_short(reason: str, missing_condition: str) -> None:
        _log_rejected_short_candidate(
            symbol=symbol,
            timeframe=timeframe,
            exchange=exchange,
            reason=reason,
            missing_condition=missing_condition,
            market_regime=market_regime_data,
        )
        signal_reviews.append(
            _short_rejection_review(
                symbol=symbol,
                timeframe=timeframe,
                exchange=exchange,
                reason=reason,
                missing_condition=missing_condition,
                market_regime=market_regime_data,
            )
        )

    close = df["close"].astype(float)
    six_candle_return = float(close.pct_change(6).iloc[-1]) if len(close) > 6 else 0.0
    last = df.iloc[-1]
    bearish_momentum = bool(market_regime_data.components.get("bearish_ema_momentum")) or (mom_ok and six_candle_return < 0)
    bullish_momentum = bool(market_regime_data.components.get("bullish_ema_momentum")) or (mom_ok and six_candle_return > 0)
    support_break = bool(market_regime_data.components.get("structural_support_break") or market_regime_data.components.get("breakdown_confirmed"))
    resistance_reclaim = bool(market_regime_data.components.get("structural_resistance_reclaim") or market_regime_data.components.get("breakout_confirmed"))
    failed_reclaim = support is not None and float(last["high"]) >= support.lower - atr * 0.35 and price < support.lower
    bearish_retest = support is not None and float(last["high"]) >= support.lower - atr * 0.5 and price < support.upper
    resistance_rejection = resistance is not None and float(last["high"]) >= resistance.lower - atr * 0.35 and price < resistance.lower
    bullish_retest = resistance is not None and float(last["low"]) <= resistance.upper + atr * 0.5 and price > resistance.lower
    support_rejection = support is not None and float(last["low"]) <= support.upper + atr * 0.35 and price > support.upper
    short_trigger_ready = any([resistance_rejection, failed_reclaim, bearish_retest, bearish_sweep is not None, support_break])
    long_trigger_ready = any([support_rejection, bullish_retest, bullish_sweep is not None, resistance_reclaim])
    short_confirmation_ready = bearish_momentum or vol_ok or support_break
    long_confirmation_ready = bullish_momentum or vol_ok or resistance_reclaim

    if regime == "RANGE_BOUND":
        if position is not None and position <= 0.25:
            sweep_low = min([float(df.loc[df["timestamp"] == bullish_sweep.candle_time, "low"].iloc[0])] if bullish_sweep is not None else [support.lower])
            append(
                _build_idea(
                    symbol=symbol,
                    timeframe=timeframe,
                    exchange=exchange,
                    direction="Long",
                    df=df,
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
                    market_regime_data=market_regime_data,
                    support=support,
                    resistance=resistance,
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
                    df=df,
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
                    market_regime_data=market_regime_data,
                    support=support,
                    resistance=resistance,
                )
            )

    if regime in {"TRENDING_UP", "TRANSITION_TO_BULLISH"} and position is not None and (
        position <= 0.4 or (regime == "TRANSITION_TO_BULLISH" and long_trigger_ready and long_confirmation_ready)
    ):
        append(
            _build_idea(
                symbol=symbol,
                timeframe=timeframe,
                exchange=exchange,
                direction="Long",
                df=df,
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
                market_regime_data=market_regime_data,
                support=support,
                resistance=resistance,
            )
        )

    if regime in {"TRENDING_DOWN", "TRANSITION_TO_BEARISH"} and position is not None:
        if position >= 0.6 or (regime == "TRANSITION_TO_BEARISH" and short_trigger_ready and short_confirmation_ready):
            entry_anchor_low = resistance.lower if resistance_rejection else price
            entry_anchor_high = resistance.upper if resistance_rejection else price + atr * 0.25
            if failed_reclaim or bearish_retest:
                entry_anchor_low = max(price, support.lower - atr * 0.35)
                entry_anchor_high = min(support.upper, price + atr * 0.45)
            append(
                _build_idea(
                    symbol=symbol,
                    timeframe=timeframe,
                    exchange=exchange,
                    direction="Short",
                    df=df,
                    regime=regime,
                    htf_bias=htf_bias,
                    entry_low=max(price, entry_anchor_low - atr * 0.45),
                    entry_high=min(entry_anchor_high, price + atr * 0.45),
                    stop=(resistance.upper if resistance_rejection else max(support.upper, price + atr * 0.8)) + stop_buffer,
                    tp1=price - atr * 2.0,
                    tp2=price - atr * 3.8,
                    zone=resistance if resistance_rejection else support,
                    sweep=bearish_sweep,
                    settings=settings,
                    vol_ok=vol_ok,
                    mom_ok=mom_ok,
                    distance_from_mid=distance_from_mid,
                    market_regime_data=market_regime_data,
                    support=support,
                    resistance=resistance,
                )
            )
        else:
            missing = []
            if position < 0.6 and not short_trigger_ready:
                missing.append("price not at resistance/retest edge")
            if not short_trigger_ready:
                missing.append("no rejection, failed reclaim, breakdown retest, support break, or bearish sweep")
            if not short_confirmation_ready:
                missing.append("no bearish momentum/volume/support-break confirmation")
            reject_short("Bearish regime short candidate was not built.", ", ".join(missing) or "short trigger not ready")

    if regime == "BREAKOUT" and (vol_ok or mom_ok):
        append(
            _build_idea(
                symbol=symbol,
                timeframe=timeframe,
                exchange=exchange,
                direction="Long",
                df=df,
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
                market_regime_data=market_regime_data,
                support=support,
                resistance=resistance,
            )
        )

    if regime == "BREAKDOWN":
        if vol_ok or mom_ok or support_break:
            append(
                _build_idea(
                    symbol=symbol,
                    timeframe=timeframe,
                    exchange=exchange,
                    direction="Short",
                    df=df,
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
                    market_regime_data=market_regime_data,
                    support=support,
                    resistance=resistance,
                )
            )
        else:
            reject_short(
                "Breakdown short candidate was not built.",
                "no bearish momentum, volume confirmation, or structural support break",
            )

    if not ideas:
        if unconfirmed and unconfirmed.confirmation_status != "confirmed":
            return [], "Unconfirmed sweep — no trade yet.", signal_reviews
        return [], "NO TRADE: setup score is below 65 or risk/reward is not good enough.", signal_reviews
    return sorted(ideas, key=lambda idea: idea.rank_score, reverse=True), None, signal_reviews


def analyze_dataframe(
    symbol: str,
    timeframe: str,
    exchange: str,
    df: pd.DataFrame,
    settings: RiskSettings,
    htf_dfs: list[pd.DataFrame] | None = None,
    global_regime_score: float | None = None,
    breadth_above_ma_pct: float | None = None,
) -> AnalysisResponse:
    supports, resistances = find_support_resistance(df)
    support, resistance = nearest_range(float(df["close"].iloc[-1]), supports, resistances)
    sweeps = detect_liquidity_sweeps(df, supports, resistances)
    htf_bias = higher_timeframe_bias(htf_dfs)
    market_regime_data = detect_market_regime(df, htf_dfs, global_score=global_regime_score, breadth_above_ma_pct=breadth_above_ma_pct)
    regime = market_regime_data.regime_type
    ideas, no_trade_reason, signal_reviews = build_trade_ideas(
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
        market_regime_data,
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
        market_regime_data=market_regime_data,
        rejected_signals=[review for review in signal_reviews if not review.accepted],
    )
