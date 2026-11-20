from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from app.models.schemas import TradeIdea
from app.strategy.edge_registry import lookup_strategy_edge, strategy_version_for_family
from app.utils.opportunities import canonical_opportunity_key, setup_family_from_regime


StrategyDecision = Literal["TRADE", "SHADOW", "NO_TRADE", "WAIT_FOR_RETEST"]


def _entry_quality(idea: TradeIdea) -> tuple[str, str]:
    if idea.entry_status == "READY":
        return "PASS", "Existing strategy entry, exhaustion, risk, and R:R checks passed."
    if idea.entry_status == "WAIT_FOR_RETEST":
        return "WAIT_FOR_RETEST", "Existing entry-quality layer requires a later executable retest."
    if idea.entry_status == "REJECTED_EXHAUSTED":
        return "REJECTED", "Existing entry-quality layer rejected an exhausted or late entry."
    return "UNKNOWN", "No independently measured entry-quality result is available."


def evaluate_strategy_decision(idea: TradeIdea) -> StrategyDecision:
    """Apply the V2 edge gate without inventing a replacement score formula."""

    family = idea.setup_family or setup_family_from_regime(str(idea.market_regime or idea.regime_type or ""))
    version = idea.strategy_version or strategy_version_for_family(family)
    regime = str(idea.market_regime or idea.regime_type or "").upper()
    edge = lookup_strategy_edge(
        strategy_family=family,
        strategy_version=version,
        regime=regime,
        direction=idea.direction,
        timeframe=idea.timeframe,
    )
    entry_quality_status, entry_quality_reason = _entry_quality(idea)

    idea.setup_family = family
    idea.strategy_version = version
    idea.regime_confidence = idea.regime_confidence_score
    idea.entry_quality_status = entry_quality_status
    # The legacy setup score blends several concepts. Keep the new independent
    # score null until a separately validated entry-quality model exists.
    idea.entry_quality_score = None
    idea.entry_quality_reason = entry_quality_reason
    idea.v2_evaluated_at = datetime.now(UTC).replace(microsecond=0)

    if edge is None:
        decision: StrategyDecision = "NO_TRADE"
        idea.edge_status = None
        reason = "No explicitly registered and supported strategy edge exists for this regime."
    else:
        idea.edge_status = edge.status
        if edge.status in {"DISABLED", "UNVALIDATED"}:
            decision = "NO_TRADE"
            reason = f"{family}:{version} is explicitly {edge.status}; regime confidence cannot activate it."
        elif idea.entry_status == "WAIT_FOR_RETEST":
            decision = "WAIT_FOR_RETEST"
            reason = f"{family}:{version} is {edge.status}, but the existing entry layer requires a retest."
        elif idea.entry_status != "READY":
            decision = "NO_TRADE"
            reason = f"Entry quality is {entry_quality_status}; only READY entries can advance."
        elif edge.status == "VALIDATED":
            decision = "TRADE"
            reason = f"{family}:{version} is explicitly VALIDATED and the existing entry/risk checks passed."
        else:
            decision = "SHADOW"
            reason = f"{family}:{version} is EXPERIMENTAL and remains non-actionable while outcomes are measured."

    idea.strategy_decision = decision
    idea.v2_decision_reason = reason
    idea.outcome_tracking_mode = "PRODUCTION" if decision == "TRADE" else "SHADOW" if decision == "SHADOW" else "NONE"
    idea.opportunity_key = canonical_opportunity_key(
        exchange=idea.exchange,
        symbol=idea.symbol,
        timeframe=idea.timeframe,
        direction=idea.direction,
        setup_family=family,
        strategy_version=version,
        signal_candle_time=idea.signal_candle_time,
    )
    if decision != "TRADE":
        idea.executable_at = None
    return decision


def is_actionable_v2(idea: TradeIdea) -> bool:
    return idea.strategy_decision == "TRADE" and idea.entry_status == "READY"
