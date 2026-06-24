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


def _rsi(close: pd.Series, period: int = 14) -> float:
    if len(close) < period + 1:
        return 50.0
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, float("nan"))
    value = 100 - (100 / (1 + rs.iloc[-1]))
    if pd.isna(value):
        return 50.0
    return float(value)


def _rolling_vwap(df: pd.DataFrame, window: int = 48) -> float:
    recent = df.tail(min(window, len(df))).copy()
    if recent.empty:
        return 0.0
    typical = (recent["high"].astype(float) + recent["low"].astype(float) + recent["close"].astype(float)) / 3
    volume = recent["volume"].astype(float).clip(lower=0)
    total_volume = float(volume.sum())
    if total_volume <= 0:
        return float(recent["close"].astype(float).ewm(span=min(20, len(recent)), adjust=False).mean().iloc[-1])
    return float((typical * volume).sum() / total_volume)


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


def _last_sweep_timestamp(df: pd.DataFrame, sweep: LiquiditySweep | None) -> int | None:
    if sweep is None or "timestamp" not in df:
        return None
    matches = df.index[df["timestamp"] == sweep.candle_time].tolist()
    return int(matches[-1]) if matches else None


def _signal_quality_control(
    *,
    direction: str,
    df: pd.DataFrame,
    score: float,
    regime: str,
    entry_low: float,
    entry_high: float,
    atr: float,
    bullish_sweep: LiquiditySweep | None,
    bearish_sweep: LiquiditySweep | None,
) -> dict[str, object]:
    if len(df) < 20 or atr <= 0:
        return {
            "score": score,
            "maturity": "Early",
            "risk": "Low",
            "status": "READY",
            "reasons": [],
            "adjustment": 0.0,
        }

    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    open_ = df["open"].astype(float)
    price = float(close.iloc[-1])
    entry_mid = (entry_low + entry_high) / 2
    lookback = min(36, len(df))
    recent_high = float(high.tail(lookback).max())
    recent_low = float(low.tail(lookback).min())
    extension = max(price - recent_low, price - entry_mid, 0.0) if direction == "Long" else max(recent_high - price, entry_mid - price, 0.0)
    extension_atr = extension / atr
    rsi = _rsi(close)
    vwap = _rolling_vwap(df)
    ema20 = float(close.ewm(span=min(20, len(close)), adjust=False).mean().iloc[-1])
    equilibrium = (vwap + ema20) / 2 if vwap else ema20

    tail = df.tail(min(8, len(df))).copy()
    ranges = high.tail(len(tail)) - low.tail(len(tail))
    bodies = (close.tail(len(tail)) - open_.tail(len(tail))).abs()
    recent_ranges = ranges.tail(3)
    previous_ranges = ranges.iloc[-6:-3] if len(ranges) >= 6 else ranges.head(3)
    recent_bodies = bodies.tail(3)
    previous_bodies = bodies.iloc[-6:-3] if len(bodies) >= 6 else bodies.head(3)
    candle_ranges_shrinking = float(recent_ranges.mean()) < float(previous_ranges.mean()) * 0.82 if len(previous_ranges) >= 2 else False
    bodies_shrinking = float(recent_bodies.mean()) < float(previous_bodies.mean()) * 0.82 if len(previous_bodies) >= 2 else False

    if direction == "Short":
        directional_candles = close.tail(4) < open_.tail(4)
        follow_through_weakening = float(low.iloc[-1]) >= float(low.tail(4).min()) or float(close.diff().tail(3).mean()) >= -atr * 0.18
        basing_after_impulse = extension_atr >= 2.4 and float(close.tail(4).max() - close.tail(4).min()) <= atr * 0.85
        momentum_decay = bool((candle_ranges_shrinking or bodies_shrinking) and (follow_through_weakening or directional_candles.sum() <= 1 or basing_after_impulse))
        far_from_equilibrium = price < equilibrium - atr * 2
        rsi_exhausted = rsi < 28
        trap_sweep = bullish_sweep is not None and bullish_sweep.confirmation_status == "confirmed"
        sweep_index = _last_sweep_timestamp(df, bullish_sweep)
    else:
        directional_candles = close.tail(4) > open_.tail(4)
        follow_through_weakening = float(high.iloc[-1]) <= float(high.tail(4).max()) or float(close.diff().tail(3).mean()) <= atr * 0.18
        topping_after_impulse = extension_atr >= 2.4 and float(close.tail(4).max() - close.tail(4).min()) <= atr * 0.85
        momentum_decay = bool((candle_ranges_shrinking or bodies_shrinking) and (follow_through_weakening or directional_candles.sum() <= 1 or topping_after_impulse))
        far_from_equilibrium = price > equilibrium + atr * 2
        rsi_exhausted = rsi > 72
        trap_sweep = bearish_sweep is not None and bearish_sweep.confirmation_status == "confirmed"
        sweep_index = _last_sweep_timestamp(df, bearish_sweep)

    if sweep_index is not None and len(df) - sweep_index > 10:
        trap_sweep = False

    reasons: list[str] = []
    penalty = 0.0
    cap: float | None = None
    status = "READY"

    exhaustion_cluster = extension_atr > 3.0 and momentum_decay and far_from_equilibrium
    if extension_atr > 3.5:
        penalty += 18 if exhaustion_cluster else 8
        if exhaustion_cluster:
            cap = min(cap or 100.0, 65.0)
        reasons.append(f"{direction} downgraded: move is extended at {extension_atr:.1f}x ATR.")
    elif extension_atr > 3.0:
        penalty += 12 if exhaustion_cluster else 6
        if exhaustion_cluster:
            cap = min(cap or 100.0, 75.0)
        reasons.append(f"{direction} downgraded: move is already extended vs ATR.")
    elif extension_atr > 2.5:
        penalty += 5
        reasons.append(f"{direction} downgraded: move is maturing vs ATR.")

    if momentum_decay:
        penalty += 15
        cap = min(cap or 100.0, 75.0)
        reasons.append(f"{direction} downgraded: {'bullish' if direction == 'Long' else 'bearish'} momentum is decaying.")

    if far_from_equilibrium:
        penalty += 15
        cap = min(cap or 100.0, 75.0)
        reasons.append(
            f"{direction} downgraded: price is far {'above' if direction == 'Long' else 'below'} VWAP/equilibrium."
        )

    if rsi_exhausted:
        penalty += 15
        cap = min(cap or 100.0, 75.0)
        reasons.append(f"{direction} downgraded: RSI shows {'upside' if direction == 'Long' else 'downside'} exhaustion.")

    if trap_sweep:
        penalty += 25
        cap = min(cap or 100.0, 60.0)
        reasons.append(
            f"{direction} rejected/downgraded: possible {'upside liquidity sweep and rejection' if direction == 'Long' else 'downside liquidity sweep and reclaim'}."
        )

    strong_extension = extension_atr > 3.0
    extreme_extension = extension_atr > 3.5
    if exhaustion_cluster or (
        regime in {"BREAKOUT", "BREAKDOWN", "TRANSITION_TO_BULLISH", "TRANSITION_TO_BEARISH"}
        and strong_extension
        and (momentum_decay or far_from_equilibrium)
    ):
        status = "WAIT_FOR_RETEST"
    if trap_sweep or (extreme_extension and momentum_decay and far_from_equilibrium and rsi_exhausted):
        status = "REJECTED_EXHAUSTED"

    adjusted = max(0.0, score - penalty)
    if cap is not None:
        adjusted = min(adjusted, cap)

    if status == "READY" and adjusted < MIN_SETUP_SCORE:
        status = "REJECTED_EXHAUSTED" if reasons else "READY"

    if exhaustion_cluster or (extension_atr > 3.5 and rsi_exhausted and (momentum_decay or far_from_equilibrium)):
        maturity = "Exhausted"
    elif extension_atr > 2.5 or momentum_decay or far_from_equilibrium or rsi_exhausted:
        maturity = "Extended"
    elif extension_atr > 1.4:
        maturity = "Mid-Trend"
    else:
        maturity = "Early"

    risk_score = sum([extension_atr > 2.5, momentum_decay, far_from_equilibrium, rsi_exhausted, trap_sweep])
    risk = "High" if status == "REJECTED_EXHAUSTED" or risk_score >= 3 else "Medium" if risk_score >= 1 else "Low"

    return {
        "score": adjusted,
        "maturity": maturity,
        "risk": risk,
        "status": status,
        "reasons": reasons,
        "adjustment": round(adjusted - score, 1),
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


def _btc_regime_label(btc_context: dict | None) -> str:
    return str((btc_context or {}).get("regime") or "unknown").lower()


def _exceptional_reversal(direction: str, score: float, market_regime: MarketRegimeSnapshot, confirmations: list[str]) -> bool:
    reclaim_key = "structure_reclaimed_bullish" if direction == "Long" else "structure_reclaimed_bearish"
    return score >= 92 and len(confirmations) >= 4 and bool(market_regime.components.get(reclaim_key))


def _context_gate(
    *,
    symbol: str,
    direction: str,
    score: float,
    market_regime: MarketRegimeSnapshot,
    confirmations: list[str],
    btc_context: dict | None,
) -> tuple[float, float, str | None, str | None]:
    bias = market_regime.bias.lower()
    btc_regime = _btc_regime_label(btc_context)
    adjusted = score
    adjustment = 0.0
    note: str | None = None
    rejected_reason: str | None = None
    exceptional = _exceptional_reversal(direction, score, market_regime, confirmations)

    if direction == "Long" and bias == "short bias" and not exceptional:
        adjustment -= 45
        adjusted += adjustment
        rejected_reason = (
            f"Long signal rejected because local bias is Short bias; score {score:.0f}, "
            f"reversal confirmations {len(confirmations)}, exceptional reversal required."
        )
    elif direction == "Short" and bias == "long bias" and not exceptional:
        adjustment -= 45
        adjusted += adjustment
        rejected_reason = (
            f"Short signal rejected because local bias is Long bias; score {score:.0f}, "
            f"reversal confirmations {len(confirmations)}, exceptional reversal required."
        )
    elif direction == "Long" and bias == "bearish transition" and not exceptional:
        adjustment -= 50
        adjusted += adjustment
        rejected_reason = (
            f"Long signal rejected because local bias is Bearish transition; score {score:.0f}, "
            f"reversal confirmations {len(confirmations)}, exceptional reversal required."
        )
    elif direction == "Short" and bias == "bullish transition" and not exceptional:
        adjustment -= 50
        adjusted += adjustment
        rejected_reason = (
            f"Short signal rejected because local bias is Bullish transition; score {score:.0f}, "
            f"reversal confirmations {len(confirmations)}, exceptional reversal required."
        )

    is_btc = symbol.upper().startswith("BTC")
    if rejected_reason is None and not is_btc:
        if direction == "Long" and btc_regime == "bearish":
            penalty = -28
            adjusted += penalty
            adjustment += penalty
            if not exceptional or adjusted < MIN_SETUP_SCORE:
                rejected_reason = (
                    f"Long signal rejected because BTC 4H/1D regime is bearish; adjusted score {adjusted:.0f}."
                )
            else:
                note = "Long heavily penalized by bearish BTC 4H/1D regime but allowed by exceptional reversal evidence."
        elif direction == "Short" and btc_regime == "bullish":
            penalty = -28
            adjusted += penalty
            adjustment += penalty
            if not exceptional or adjusted < MIN_SETUP_SCORE:
                rejected_reason = (
                    f"Short signal rejected because BTC 4H/1D regime is bullish; adjusted score {adjusted:.0f}."
                )
            else:
                note = "Short heavily penalized by bullish BTC 4H/1D regime but allowed by exceptional reversal evidence."

    if rejected_reason:
        logger.info(
            "Signal rejected pair=%s direction=%s bias=%s btc_regime=%s confidence=%.1f rejection_reason=%s",
            symbol,
            direction,
            market_regime.bias,
            btc_regime,
            score,
            rejected_reason,
        )
    return max(0.0, adjusted), adjustment, rejected_reason, note


def _clone_regime_for_setup(
    market_regime: MarketRegimeSnapshot,
    *,
    regime_type: str,
    trade_decision: str = "TRADE_ALLOWED",
    note: str,
) -> MarketRegimeSnapshot:
    return market_regime.model_copy(
        update={
            "regime_type": regime_type,
            "trade_decision": trade_decision,
            "is_transition": regime_type in {"TRANSITION_TO_BULLISH", "TRANSITION_TO_BEARISH"},
            "explanation": f"{market_regime.explanation} Softened for setup construction: {note}",
        }
    )


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
    bullish_sweep: LiquiditySweep | None,
    bearish_sweep: LiquiditySweep | None,
    btc_context: dict | None = None,
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
    quality = _signal_quality_control(
        direction=direction,
        df=df,
        score=adjusted_score,
        regime=regime,
        entry_low=entry_low,
        entry_high=entry_high,
        atr=average_true_range(df),
        bullish_sweep=bullish_sweep,
        bearish_sweep=bearish_sweep,
    )
    quality_score = float(quality["score"])
    quality_adjustment = float(quality["adjustment"])
    quality_reasons = list(quality["reasons"])
    entry_status = str(quality["status"])
    if quality_reasons:
        confidence_adjustment += quality_adjustment
        adjusted_score = quality_score
    if rejected_reason is None and entry_status == "REJECTED_EXHAUSTED":
        rejected_reason = "Signal rejected because exhaustion filters show the move is already too mature. " + " ".join(quality_reasons)
    if rejected_reason is None:
        context_score, context_adjustment, context_rejection, context_note = _context_gate(
            symbol=symbol,
            direction=direction,
            score=adjusted_score,
            market_regime=market_regime_data,
            confirmations=confirmations,
            btc_context=btc_context,
        )
        if context_adjustment:
            adjusted_score = context_score
            confidence_adjustment += context_adjustment
        if context_rejection:
            rejected_reason = context_rejection
        elif context_note:
            regime_note = f"{regime_note} {context_note}" if regime_note else context_note
    if rejected_reason is None and adjusted_score < MIN_SETUP_SCORE:
        if quality_reasons:
            rejected_reason = "Signal rejected because setup score is below 65 after exhaustion quality control. " + " ".join(quality_reasons)
        else:
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
    if quality_reasons:
        reason = f"{reason} {' '.join(quality_reasons)} Entry Status: {entry_status}."
    reason = f"{reason} Market Regime: {market_regime_data.label} ({market_regime_data.score:+.0f}); trade is {trend_alignment}; confidence adjustment {confidence_adjustment:+.0f}."

    idea = TradeIdea(
        symbol=symbol,
        timeframe=timeframe,
        exchange=exchange,
        source=exchange,
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
        rank_score=round(adjusted_score * 1.4 + rr * 6 + (sweep.sweep_quality_score or 0 if sweep else 0) * 0.15 + (zone.strength_score or 0) * 0.1, 2),
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
        move_maturity=str(quality["maturity"]),
        exhaustion_risk=str(quality["risk"]),
        entry_status=entry_status,
        downgraded_reasons=quality_reasons,
        signal_candle_time=df["timestamp"].iloc[-1],
    )
    review = SignalReview(
        symbol=symbol,
        timeframe=timeframe,
        exchange=exchange,
        direction=direction,
        accepted=True,
        reason="Signal accepted after market-regime and exhaustion quality control.",
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
    btc_context: dict | None = None,
) -> tuple[list[TradeIdea], str | None, list[SignalReview]]:
    if support is None or resistance is None:
        return [], "NO TRADE: not enough clean support/resistance structure.", []

    price = float(df["close"].iloc[-1])
    atr = average_true_range(df)
    range_width = resistance.lower - support.upper
    structural_support_break = bool(
        market_regime_data.components.get("structural_support_break")
        or market_regime_data.components.get("breakdown_confirmed")
    )
    structural_resistance_reclaim = bool(
        market_regime_data.components.get("structural_resistance_reclaim")
        or market_regime_data.components.get("breakout_confirmed")
    )
    if range_width <= atr * 1.4 and not (
        (regime in {"BREAKDOWN", "TRANSITION_TO_BEARISH"} and structural_support_break)
        or (regime in {"BREAKOUT", "TRANSITION_TO_BULLISH"} and structural_resistance_reclaim)
    ):
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
    support_break = structural_support_break
    resistance_reclaim = structural_resistance_reclaim
    failed_reclaim = support is not None and float(last["high"]) >= support.lower - atr * 0.35 and price < support.lower
    bearish_retest = support is not None and float(last["high"]) >= support.lower - atr * 0.5 and price < support.upper
    resistance_rejection = resistance is not None and float(last["high"]) >= resistance.lower - atr * 0.35 and price < resistance.lower
    bullish_retest = resistance is not None and float(last["low"]) <= resistance.upper + atr * 0.5 and price > resistance.lower
    support_rejection = support is not None and float(last["low"]) <= support.upper + atr * 0.35 and price > support.upper
    short_trigger_ready = any([resistance_rejection, failed_reclaim, bearish_retest, bearish_sweep is not None, support_break])
    long_trigger_ready = any([support_rejection, bullish_retest, bullish_sweep is not None, resistance_reclaim])
    short_confirmation_ready = bearish_momentum or vol_ok or support_break
    long_confirmation_ready = bullish_momentum or vol_ok or resistance_reclaim
    normal_volatility = 0.002 <= atr / max(price, 1e-9) <= 0.12
    near_long_edge = position is not None and position <= 0.4
    near_short_edge = position is not None and position >= 0.6

    def evidence(direction: str) -> list[str]:
        items: list[str] = []
        if direction == "Long":
            if bullish_sweep is not None or any(sweep.direction == "bullish" for sweep in sweeps):
                items.append("sweep hint")
            if resistance_reclaim:
                items.append("reclaim hint")
            if bullish_retest:
                items.append("retest hint")
            if bullish_momentum:
                items.append("momentum agrees")
            if vol_ok:
                items.append("volume confirms")
            if htf_bias in {"HTF_BULLISH", "HTF_NEUTRAL"}:
                items.append("HTF bias agrees")
            if support_rejection:
                items.append("price reacts at valid zone")
        else:
            if bearish_sweep is not None or any(sweep.direction == "bearish" for sweep in sweeps):
                items.append("sweep hint")
            if resistance_rejection or failed_reclaim:
                items.append("rejection hint")
            if bearish_retest:
                items.append("retest hint")
            if bearish_momentum:
                items.append("momentum agrees")
            if vol_ok:
                items.append("volume confirms")
            if htf_bias in {"HTF_BEARISH", "HTF_NEUTRAL"}:
                items.append("HTF bias agrees")
            if resistance_rejection or failed_reclaim:
                items.append("price reacts at valid zone")
        return list(dict.fromkeys(items))

    long_evidence = evidence("Long")
    short_evidence = evidence("Short")
    long_evidence_ready = len(long_evidence) >= 2
    short_evidence_ready = len(short_evidence) >= 2
    original_decision = market_regime_data.trade_decision
    original_regime = regime
    hard_chop = market_regime_data.regime_type == "CHOP" and not (near_long_edge or near_short_edge)
    invalid_structure = "invalid" in market_regime_data.structure.lower() or "insufficient" in market_regime_data.structure.lower()
    dead_volatility = atr / max(price, 1e-9) < 0.0015

    if market_regime_data.trade_decision in {"WAIT", "NO_TRADE"}:
        soften_note = ""
        softened_regime = None
        if (
            normal_volatility
            and not invalid_structure
            and not dead_volatility
            and not hard_chop
            and (near_long_edge or resistance_reclaim or bullish_retest)
            and long_evidence_ready
        ):
            softened_regime = "TRANSITION_TO_BULLISH" if market_regime_data.trade_decision == "WAIT" else "RANGE_BOUND"
            soften_note = f"long evidence at support/edge: {', '.join(long_evidence)}"
        elif (
            normal_volatility
            and not invalid_structure
            and not dead_volatility
            and not hard_chop
            and (near_short_edge or support_break or bearish_retest or failed_reclaim)
            and short_evidence_ready
        ):
            softened_regime = "TRANSITION_TO_BEARISH" if market_regime_data.trade_decision == "WAIT" else "RANGE_BOUND"
            soften_note = f"short evidence at resistance/edge: {', '.join(short_evidence)}"
        if softened_regime is None:
            logger.info(
                "No setup generation symbol=%s timeframe=%s exchange=%s bias=%s reason=%s flip_trigger=%s decision=%s long_evidence=%s short_evidence=%s",
                symbol,
                timeframe,
                exchange,
                market_regime_data.bias,
                market_regime_data.bias_reason,
                market_regime_data.bias_flip_trigger,
                market_regime_data.trade_decision,
                long_evidence,
                short_evidence,
            )
            return [], (
                f"WAIT / NO TRADE: {market_regime_data.label} lacks edge/evidence for setup construction. "
                f"{market_regime_data.explanation}"
            ), []
        market_regime_data = _clone_regime_for_setup(market_regime_data, regime_type=softened_regime, note=soften_note)
        regime = softened_regime

    if original_decision == "WAIT" and regime == "RANGE_BOUND":
        regime = "TRANSITION_TO_BULLISH" if near_long_edge and long_evidence_ready else "TRANSITION_TO_BEARISH" if near_short_edge and short_evidence_ready else regime

    if regime == "RANGE_BOUND":
        if position is not None and position <= 0.35 and long_evidence_ready:
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
                    bullish_sweep=bullish_sweep,
                    bearish_sweep=bearish_sweep,
                    btc_context=btc_context,
                )
            )
        elif position is not None and position >= 0.65 and short_evidence_ready:
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
                    bullish_sweep=bullish_sweep,
                    bearish_sweep=bearish_sweep,
                    btc_context=btc_context,
                )
            )

    if regime in {"TRENDING_UP", "TRANSITION_TO_BULLISH"} and position is not None and long_evidence_ready and (
        position <= 0.5
        or (regime == "TRANSITION_TO_BULLISH" and long_trigger_ready and long_confirmation_ready)
        or (original_regime in {"TRENDING_UP", "BREAKOUT"} and bullish_retest and bullish_momentum)
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
                bullish_sweep=bullish_sweep,
                bearish_sweep=bearish_sweep,
                btc_context=btc_context,
            )
        )

    if regime in {"TRENDING_DOWN", "TRANSITION_TO_BEARISH"} and position is not None:
        if short_evidence_ready and (
            position >= 0.5
            or (regime == "TRANSITION_TO_BEARISH" and short_trigger_ready and short_confirmation_ready)
            or (original_regime in {"TRENDING_DOWN", "BREAKDOWN"} and (bearish_retest or failed_reclaim) and bearish_momentum)
        ):
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
                    bullish_sweep=bullish_sweep,
                    bearish_sweep=bearish_sweep,
                    btc_context=btc_context,
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

    if regime == "BREAKOUT" and (vol_ok or mom_ok) and long_evidence_ready:
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
                bullish_sweep=bullish_sweep,
                bearish_sweep=bearish_sweep,
                btc_context=btc_context,
            )
        )

    if regime == "BREAKDOWN":
        if short_evidence_ready and (vol_ok or mom_ok or support_break):
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
                    bullish_sweep=bullish_sweep,
                    bearish_sweep=bearish_sweep,
                    btc_context=btc_context,
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
    btc_context: dict | None = None,
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
        btc_context,
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
