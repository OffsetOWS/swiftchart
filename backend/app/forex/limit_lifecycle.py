from __future__ import annotations

from datetime import UTC, datetime

from app.forex.models import ForexLimitOpportunity

TERMINAL_LIMIT_STATUSES = frozenset(
    {"TP2_HIT", "SL_HIT", "EXPIRED", "CANCELLED", "INVALIDATED", "MISSED_NO_RETEST", "TARGET_REACHED_BEFORE_ENTRY", "NEWS_CANCELLED"}
)
PENDING_LIMIT_STATUSES = frozenset({"WAIT_FOR_RETEST", "PENDING_LIMIT"})


def advance_limit_opportunity(
    opportunity: ForexLimitOpportunity,
    *,
    candle_high: float,
    candle_low: float,
    candle_close: float,
    candle_time: datetime,
    news_lockout: bool = False,
    spread_ok: bool = True,
    structure_valid: bool = True,
    fvg_valid: bool = True,
    htf_bias_valid: bool = True,
    chase_valid: bool = True,
) -> ForexLimitOpportunity:
    """Idempotent candle transition; pending orders cannot accrue PnL or losses."""
    if opportunity.opportunity_status in TERMINAL_LIMIT_STATUSES:
        return opportunity
    at = candle_time.astimezone(UTC) if candle_time.tzinfo else candle_time.replace(tzinfo=UTC)
    next_status = opportunity.opportunity_status
    cancellation_reason = opportunity.cancellation_reason
    invalidation_reason = opportunity.invalidation_reason
    fill_time = opportunity.fill_time
    closed_at = opportunity.closed_at
    mae_pips = opportunity.mae_pips
    mfe_pips = opportunity.mfe_pips
    pnl_r = opportunity.pnl_r

    if opportunity.opportunity_status in PENDING_LIMIT_STATUSES:
        if news_lockout:
            next_status, cancellation_reason, closed_at = "NEWS_CANCELLED", "High-impact news lockout began before fill.", at
        elif not spread_ok:
            next_status, cancellation_reason, closed_at = "CANCELLED", "Spread became unacceptable before fill.", at
        elif not structure_valid:
            next_status, invalidation_reason, closed_at = "INVALIDATED", "Market structure invalidated before fill.", at
        elif not fvg_valid:
            next_status, invalidation_reason, closed_at = "INVALIDATED", "The fair value gap was fully invalidated before fill.", at
        elif not htf_bias_valid:
            next_status, invalidation_reason, closed_at = "INVALIDATED", "Higher-timeframe bias strongly reversed before fill.", at
        elif not chase_valid:
            next_status, cancellation_reason, closed_at = "MISSED_NO_RETEST", "Price moved too far away without retesting the limit.", at
        elif at >= opportunity.expiry_time:
            next_status, cancellation_reason, closed_at = "EXPIRED", "Limit expired without entry; no trade was opened.", at
        else:
            long_side = opportunity.direction == "LONG"
            target_reached = candle_high >= opportunity.take_profit_1 if long_side else candle_low <= opportunity.take_profit_1
            invalidated = candle_close <= opportunity.sweep_extreme if long_side else candle_close >= opportunity.sweep_extreme
            filled = candle_low <= opportunity.entry_price if long_side else candle_high >= opportunity.entry_price
            if target_reached:
                next_status, cancellation_reason, closed_at = (
                    "TARGET_REACHED_BEFORE_ENTRY", "TP1 traded before the pending limit filled.", at
                )
            elif invalidated:
                next_status, invalidation_reason, closed_at = (
                    "INVALIDATED", "Price closed beyond the sweep extreme before entry.", at
                )
            elif filled:
                next_status, fill_time = "ACTIVE_TRADE", at
    elif opportunity.opportunity_status in {"ACTIVE_TRADE", "TP1_HIT"}:
        long_side = opportunity.direction == "LONG"
        pip_size = abs(opportunity.entry_price - opportunity.stop_loss) / opportunity.risk_pips
        favorable = (
            max(0.0, candle_high - opportunity.entry_price)
            if long_side
            else max(0.0, opportunity.entry_price - candle_low)
        ) / pip_size
        adverse = (
            max(0.0, opportunity.entry_price - candle_low)
            if long_side
            else max(0.0, candle_high - opportunity.entry_price)
        ) / pip_size
        mfe_pips = max(mfe_pips, favorable)
        mae_pips = max(mae_pips, adverse)
        stopped = candle_low <= opportunity.stop_loss if long_side else candle_high >= opportunity.stop_loss
        tp2 = candle_high >= opportunity.take_profit_2 if long_side else candle_low <= opportunity.take_profit_2
        tp1 = candle_high >= opportunity.take_profit_1 if long_side else candle_low <= opportunity.take_profit_1
        if stopped:
            next_status, closed_at, pnl_r = "SL_HIT", at, -1.0
        elif tp2:
            next_status, closed_at, pnl_r = "TP2_HIT", at, opportunity.risk_reward_2
        elif tp1:
            next_status = "TP1_HIT"

    status_changed = next_status != opportunity.opportunity_status
    metrics_changed = (
        round(mae_pips, 1) != opportunity.mae_pips
        or round(mfe_pips, 1) != opportunity.mfe_pips
        or pnl_r != opportunity.pnl_r
    )
    if not status_changed and not metrics_changed:
        return opportunity
    events = opportunity.lifecycle_events
    if status_changed:
        events = [*events, {"event": next_status, "at": at.isoformat()}]
    return opportunity.model_copy(
        update={
            "opportunity_status": next_status,
            "fill_time": fill_time,
            "closed_at": closed_at,
            "cancellation_reason": cancellation_reason,
            "invalidation_reason": invalidation_reason,
            "current_price": candle_close,
            "updated_at": at,
            "mae_pips": round(mae_pips, 1),
            "mfe_pips": round(mfe_pips, 1),
            "pnl_r": pnl_r,
            "lifecycle_events": events,
        }
    )
