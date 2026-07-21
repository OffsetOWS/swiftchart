from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

from app.utils.opportunities import SetupFamily


StrategyEdgeStatus = Literal["VALIDATED", "EXPERIMENTAL", "UNVALIDATED", "DISABLED"]


@dataclass(frozen=True)
class StrategyEdge:
    """Explicit production-control snapshot for one crypto strategy version.

    Performance evidence is descriptive. ``status`` is the independently
    controlled production switch; no metric in this record can mutate it.
    """

    strategy_family: SetupFamily
    strategy_version: str
    regime: str
    direction: str | None
    timeframe: str | None
    status: StrategyEdgeStatus
    sample_size: int | None
    completed_sample_size: int | None
    win_rate: float | None
    expectancy_r: float | None
    total_r: float | None
    last_evaluated_at: str
    validation_source: str
    validation_reason: str

    def to_dict(self) -> dict:
        return asdict(self)


# This is an explicit, versioned production registry—not an auto-validation
# rule. Values summarize the latest pre-V2 audit and must be updated through a
# reviewed code/configuration change. Unknown sample counts remain null rather
# than being inferred from aggregate percentages.
_REGISTRY: tuple[StrategyEdge, ...] = (
    StrategyEdge(
        strategy_family="range_mean_reversion",
        strategy_version="v1",
        regime="RANGE_BOUND",
        direction=None,
        timeframe=None,
        status="VALIDATED",
        sample_size=None,
        completed_sample_size=None,
        win_rate=44.08,
        expectancy_r=0.1938,
        total_r=None,
        last_evaluated_at="2026-07-21T00:00:00+00:00",
        validation_source="pre_v2_historical_audit",
        validation_reason="Only current strategy family with demonstrated positive historical and opportunity-level expectancy.",
    ),
    StrategyEdge(
        strategy_family="trend_continuation",
        strategy_version="v1",
        regime="ANY",
        direction=None,
        timeframe=None,
        status="EXPERIMENTAL",
        sample_size=None,
        completed_sample_size=None,
        win_rate=30.17,
        expectancy_r=-0.3146,
        total_r=None,
        last_evaluated_at="2026-07-21T00:00:00+00:00",
        validation_source="pre_v2_historical_audit",
        validation_reason="Aggregate trend continuation expectancy is negative; LONG and SHORT require independent forward validation.",
    ),
    StrategyEdge(
        strategy_family="breakout",
        strategy_version="v1",
        regime="ANY",
        direction=None,
        timeframe=None,
        status="DISABLED",
        sample_size=None,
        completed_sample_size=None,
        win_rate=29.17,
        expectancy_r=-0.4028,
        total_r=None,
        last_evaluated_at="2026-07-21T00:00:00+00:00",
        validation_source="pre_v2_historical_audit",
        validation_reason="Current breakout implementation has materially negative historical and opportunity-level expectancy.",
    ),
    StrategyEdge(
        strategy_family="regime_transition",
        strategy_version="v1",
        regime="ANY",
        direction=None,
        timeframe=None,
        status="UNVALIDATED",
        sample_size=None,
        completed_sample_size=None,
        win_rate=35.52,
        expectancy_r=-0.0929,
        total_r=None,
        last_evaluated_at="2026-07-21T00:00:00+00:00",
        validation_source="pre_v2_historical_audit",
        validation_reason="Transition setups have not demonstrated a validated positive edge and default to NO_TRADE.",
    ),
)


def strategy_version_for_family(family: SetupFamily | None) -> str | None:
    return "v1" if family is not None else None


def edge_registry() -> list[dict]:
    return [entry.to_dict() for entry in _REGISTRY]


def lookup_strategy_edge(
    *,
    strategy_family: SetupFamily | None,
    strategy_version: str | None,
    regime: str | None,
    direction: str | None,
    timeframe: str | None,
) -> StrategyEdge | None:
    if strategy_family is None or strategy_version is None:
        return None
    normalized_regime = str(regime or "").upper()
    normalized_direction = str(direction or "").upper() or None
    normalized_timeframe = str(timeframe or "").lower() or None
    candidates = [
        entry
        for entry in _REGISTRY
        if entry.strategy_family == strategy_family and entry.strategy_version == strategy_version
    ]
    matches = [
        entry
        for entry in candidates
        if entry.regime in {"ANY", normalized_regime}
        and entry.direction in {None, normalized_direction}
        and entry.timeframe in {None, normalized_timeframe}
    ]
    if not matches:
        return None
    return max(
        matches,
        key=lambda entry: (
            entry.regime != "ANY",
            entry.direction is not None,
            entry.timeframe is not None,
        ),
    )
