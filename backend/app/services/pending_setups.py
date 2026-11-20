from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd

from app.models.schemas import AnalysisResponse, Direction, PendingSetup, PendingSetupStatus, TradeIdea, Zone
from app.strategy.edge_registry import lookup_strategy_edge, strategy_version_for_family
from app.strategy.market_structure import momentum_confirmation, range_position, volume_confirmation
from app.strategy.support_resistance import average_true_range, nearest_range
from app.utils.opportunities import setup_family_from_regime


EDGE_LONG_MAX = 0.35
EDGE_SHORT_MIN = 0.65
ALLOWED_WATCH_REGIMES = {
    "RANGE_BOUND",
    "TRENDING_UP",
    "TRENDING_DOWN",
    "BREAKOUT",
    "BREAKDOWN",
    "TRANSITION_TO_BULLISH",
    "TRANSITION_TO_BEARISH",
}


def _zone_tuple(zone: Zone | None) -> tuple[float, float] | None:
    if zone is None:
        return None
    return (round(float(zone.lower), 8), round(float(zone.upper), 8))


def _zone_mid(zone: Zone) -> float:
    return (float(zone.lower) + float(zone.upper)) / 2


def _direction_from_context(regime_type: str, position: float | None, bullish_hints: list[str], bearish_hints: list[str]) -> Direction | None:
    if position is not None:
        if position <= EDGE_LONG_MAX and bullish_hints:
            return "Long"
        if position >= EDGE_SHORT_MIN and bearish_hints:
            return "Short"
    if regime_type in {"TRANSITION_TO_BULLISH", "TRENDING_UP", "BREAKOUT"} and bullish_hints:
        return "Long"
    if regime_type in {"TRANSITION_TO_BEARISH", "TRENDING_DOWN", "BREAKDOWN"} and bearish_hints:
        return "Short"
    if len(bullish_hints) > len(bearish_hints):
        return "Long"
    if len(bearish_hints) > len(bullish_hints):
        return "Short"
    return None


def _hard_no_trade(
    *,
    analysis: AnalysisResponse,
    support: Zone | None,
    resistance: Zone | None,
    position: float | None,
    atr: float,
    price: float,
) -> bool:
    regime = analysis.market_regime_data
    if support is None or resistance is None:
        return True
    if atr <= 0 or price <= 0:
        return True
    if atr / price < 0.0015:
        return True
    width = max(float(resistance.lower) - float(support.upper), 0.0)
    if width <= atr * 1.05:
        return True
    if position is not None and 0.38 < position < 0.62:
        return True
    if regime and regime.trade_decision == "NO_TRADE":
        blocked_structure = "chop" in regime.label.lower() or regime.regime_type == "CHOP"
        invalid_structure = "invalid" in regime.structure.lower() or "insufficient" in regime.structure.lower()
        if blocked_structure or invalid_structure:
            return True
    return False


def _trigger_hints(
    *,
    analysis: AnalysisResponse,
    df: pd.DataFrame,
    support: Zone,
    resistance: Zone,
    position: float | None,
    atr: float,
) -> tuple[list[str], list[str]]:
    last = df.iloc[-1]
    close = float(last["close"])
    high = float(last["high"])
    low = float(last["low"])
    open_ = float(last["open"])
    bullish: list[str] = []
    bearish: list[str] = []

    for sweep in analysis.liquidity_sweeps[-4:]:
        if sweep.confirmation_status == "confirmed":
            continue
        if sweep.direction == "bullish":
            bullish.append("unconfirmed sweep below support")
        elif sweep.direction == "bearish":
            bearish.append("unconfirmed sweep above resistance")

    near_support = position is not None and position <= EDGE_LONG_MAX
    near_resistance = position is not None and position >= EDGE_SHORT_MIN
    support_buffer = max(atr * 0.55, close * 0.0015)
    resistance_buffer = max(atr * 0.55, close * 0.0015)

    if near_support and low <= support.upper + support_buffer and close >= support.lower:
        bullish.append("support reaction hint")
    if near_support and low < support.lower and close > support.upper:
        bullish.append("reclaim hint")
    if near_resistance and high >= resistance.lower - resistance_buffer and close <= resistance.upper:
        bearish.append("resistance reaction hint")
    if near_resistance and high > resistance.upper and close < resistance.lower:
        bearish.append("rejection hint")

    body = close - open_
    recent_return = float(df["close"].astype(float).pct_change(6).iloc[-1]) if len(df) > 6 else 0.0
    vol_ok = volume_confirmation(df)
    mom_ok = momentum_confirmation(df)
    if mom_ok or vol_ok:
        if recent_return > 0.008 or body > atr * 0.35:
            bullish.append("momentum continuation hint")
        if recent_return < -0.008 or body < -atr * 0.35:
            bearish.append("momentum continuation hint")

    if near_support and abs(close - support.upper) <= support_buffer:
        bullish.append("retest hint")
    if near_resistance and abs(close - resistance.lower) <= resistance_buffer:
        bearish.append("retest hint")

    return list(dict.fromkeys(bullish)), list(dict.fromkeys(bearish))


def _status_for_hints(direction: Direction, regime_type: str, hints: list[str]) -> PendingSetupStatus:
    hint_text = " ".join(hints)
    if "unconfirmed sweep" in hint_text:
        return "UNCONFIRMED_SWEEP"
    if regime_type in {"TRENDING_UP", "TRENDING_DOWN", "BREAKOUT", "BREAKDOWN"} and (
        "momentum continuation hint" in hint_text or "retest hint" in hint_text or "reaction hint" in hint_text
    ):
        return "CONTINUATION_WATCH"
    if "retest hint" in hint_text:
        return "WAITING_FOR_RETEST"
    if direction and hints:
        return "NEEDS_TRIGGER"
    return "WATCHING"


def _confirmation_needed(status: PendingSetupStatus, direction: Direction) -> list[str]:
    if status == "UNCONFIRMED_SWEEP":
        return ["Sweep confirmation close", "Follow-through away from liquidity"]
    if status == "WAITING_FOR_RETEST":
        return ["Retest hold", "Trigger candle close"]
    if status == "CONTINUATION_WATCH":
        return ["Trend retest holds", "Momentum continuation close"]
    if direction == "Long":
        return ["Bullish trigger candle", "Price holds support or reclaim"]
    return ["Bearish trigger candle", "Price rejects resistance or loses support"]


def _rr_preview(direction: Direction, entry: float, invalidation: float, target: float) -> float | None:
    risk = abs(entry - invalidation)
    if risk <= 0:
        return None
    reward = target - entry if direction == "Long" else entry - target
    return round(max(0.0, reward / risk), 2)


def _score_preview(*, position: float | None, regime_confidence: float, zone: Zone, hints: list[str], estimated_rr: float | None) -> float:
    edge_points = 0.0
    if position is not None:
        edge_points = min(18.0, abs(position - 0.5) * 36)
    zone_points = min(18.0, float(zone.strength_score or zone.strength * 100) / 5)
    hint_points = min(24.0, len(hints) * 8.0)
    regime_points = min(16.0, max(0.0, regime_confidence) / 6.25)
    rr_points = min(9.0, (estimated_rr or 0.0) * 3)
    return round(min(64.0, 18 + edge_points + zone_points + hint_points + regime_points + rr_points), 1)


def build_pending_setup(analysis: AnalysisResponse, df: pd.DataFrame) -> PendingSetup | None:
    """Build a website-only watchlist item without changing strategy acceptance rules."""
    if len(df) < 40 or analysis.trade_ideas:
        return None
    regime = analysis.market_regime_data
    if regime is None:
        return None
    regime_type = regime.regime_type
    if regime_type not in ALLOWED_WATCH_REGIMES and regime.trade_decision != "WAIT":
        return None

    price = float(df["close"].astype(float).iloc[-1])
    atr = average_true_range(df)
    support, resistance = nearest_range(price, analysis.support_zones, analysis.resistance_zones)
    position = range_position(price, support, resistance)
    if _hard_no_trade(analysis=analysis, support=support, resistance=resistance, position=position, atr=atr, price=price):
        return None
    assert support is not None
    assert resistance is not None

    bullish_hints, bearish_hints = _trigger_hints(
        analysis=analysis,
        df=df,
        support=support,
        resistance=resistance,
        position=position,
        atr=atr,
    )
    direction = _direction_from_context(regime_type, position, bullish_hints, bearish_hints)
    if direction is None:
        return None
    family = setup_family_from_regime(regime_type)
    strategy_version = strategy_version_for_family(family)
    edge = lookup_strategy_edge(
        strategy_family=family,
        strategy_version=strategy_version,
        regime=regime_type,
        direction=direction,
        timeframe=analysis.timeframe,
    )
    if edge is None or edge.status in {"DISABLED", "UNVALIDATED"}:
        return None
    hints = bullish_hints if direction == "Long" else bearish_hints
    if not hints:
        return None

    zone = support if direction == "Long" else resistance
    opposite = resistance if direction == "Long" else support
    entry_zone = _zone_tuple(zone)
    if entry_zone is None:
        return None
    invalidation = round(float(zone.lower - atr * 0.35) if direction == "Long" else float(zone.upper + atr * 0.35), 8)
    entry_mid = _zone_mid(zone)
    target = _zone_mid(opposite)
    estimated_rr = _rr_preview(direction, entry_mid, invalidation, target)
    status = _status_for_hints(direction, regime_type, hints)
    confirmation_needed = _confirmation_needed(status, direction)
    reason = f"{regime.label}: {direction.lower()} watch near {'support' if direction == 'Long' else 'resistance'} with {', '.join(hints[:2])}."

    return PendingSetup(
        symbol=analysis.symbol,
        direction=direction,
        regime=regime.label or regime_type,
        status=status,
        reason=reason,
        price=round(price, 8),
        entry_zone=entry_zone,
        invalidation_level=invalidation,
        nearest_support=_zone_tuple(support),
        nearest_resistance=_zone_tuple(resistance),
        price_position=round(position, 3) if position is not None else None,
        trigger_hints=hints,
        confirmation_needed=confirmation_needed,
        estimated_rr=estimated_rr,
        score_preview=_score_preview(
            position=position,
            regime_confidence=regime.confidence_score,
            zone=zone,
            hints=hints,
            estimated_rr=estimated_rr,
        ),
        timeframe=analysis.timeframe,
        exchange=analysis.exchange,
        created_at=datetime.now(UTC),
        setup_family=family,
        strategy_version=strategy_version,
        edge_status=edge.status,
        strategy_decision="WAIT_FOR_RETEST",
    )


def pending_setup_from_trade_idea(idea: TradeIdea) -> PendingSetup:
    """Expose a persisted WAIT_FOR_RETEST idea as a non-actionable watch item."""
    entry_mid = sum(idea.entry_zone) / 2
    return PendingSetup(
        symbol=idea.symbol,
        direction=idea.direction,
        regime=idea.regime_label or str(idea.market_regime or "Pending retest"),
        status="WAITING_FOR_RETEST",
        reason=idea.reason,
        price=round(float(idea.signal_candle_close or entry_mid), 8),
        entry_zone=idea.entry_zone,
        invalidation_level=idea.stop_loss,
        trigger_hints=["setup detected; retest not yet confirmed"],
        confirmation_needed=["Later candle touches the original entry zone", "Directional confirmation close", "Setup quality returns READY"],
        estimated_rr=idea.risk_reward_ratio,
        score_preview=float(idea.setup_score or idea.confidence_score),
        timeframe=idea.timeframe,
        exchange=idea.exchange,
        created_at=datetime.now(UTC),
        setup_family=idea.setup_family,
        strategy_version=idea.strategy_version,
        edge_status=idea.edge_status,
        strategy_decision=idea.strategy_decision,
        opportunity_key=idea.opportunity_key,
        signal_candle_time=idea.signal_candle_time,
    )
