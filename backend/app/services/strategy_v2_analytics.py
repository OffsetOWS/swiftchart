from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime

from app.strategy.edge_registry import edge_registry
from app.utils.database import get_connection


COMPLETED_RESULTS = {"WIN", "PARTIAL_WIN", "PARTIAL_LOSS", "LOSS", "BREAK_EVEN"}
GROUP_FIELDS = (
    "setup_family",
    "strategy_version",
    "market_regime",
    "direction",
    "timeframe",
    "edge_status",
    "strategy_decision",
)


def _canonical_rows(rows: list[dict]) -> list[dict]:
    canonical: dict[str, dict] = {}
    for row in rows:
        key = str(row.get("opportunity_key") or f"v2-row:{row['id']}")
        existing = canonical.get(key)
        if existing is None or int(row["id"]) < int(existing["id"]):
            canonical[key] = row
    return list(canonical.values())


def _metrics(rows: list[dict]) -> dict:
    completed = [
        row
        for row in rows
        if row.get("result") in COMPLETED_RESULTS and row.get("pnl_r_multiple") is not None
    ]
    wins = [row for row in completed if float(row["pnl_r_multiple"]) > 0]
    losses = [row for row in completed if float(row["pnl_r_multiple"]) < 0]
    total_r = sum(float(row["pnl_r_multiple"]) for row in completed)
    return {
        "detected_opportunities": len(rows),
        "actionable_opportunities": sum(row.get("strategy_decision") == "TRADE" for row in rows),
        "shadow_opportunities": sum(row.get("strategy_decision") == "SHADOW" for row in rows),
        "no_trade_decisions": sum(row.get("strategy_decision") == "NO_TRADE" for row in rows),
        "pending_retests": sum(row.get("strategy_decision") == "WAIT_FOR_RETEST" for row in rows),
        "completed_opportunities": len(completed),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / (len(wins) + len(losses)) * 100, 2) if wins or losses else 0.0,
        "average_winning_r": round(sum(float(row["pnl_r_multiple"]) for row in wins) / len(wins), 4) if wins else 0.0,
        "average_losing_r": round(sum(float(row["pnl_r_multiple"]) for row in losses) / len(losses), 4) if losses else 0.0,
        "expectancy_r": round(total_r / len(completed), 4) if completed else 0.0,
        "total_r": round(total_r, 4),
    }


def strategy_v2_performance_report() -> dict:
    """Read-only forward report. Historical rows without a version are excluded."""
    with get_connection() as connection:
        rows = [
            dict(row)
            for row in connection.execute(
                "SELECT * FROM trade_ideas WHERE strategy_version IS NOT NULL ORDER BY created_at, id"
            ).fetchall()
        ]
    canonical = _canonical_rows(rows)
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for row in canonical:
        groups[tuple(row.get(field) for field in GROUP_FIELDS)].append(row)
    breakdown = []
    for key, group_rows in groups.items():
        dimensions = dict(zip(GROUP_FIELDS, key, strict=True))
        breakdown.append({**dimensions, **_metrics(group_rows)})
    breakdown.sort(
        key=lambda item: (
            str(item.get("setup_family") or ""),
            str(item.get("strategy_version") or ""),
            str(item.get("market_regime") or ""),
            str(item.get("direction") or ""),
            str(item.get("timeframe") or ""),
            str(item.get("strategy_decision") or ""),
        )
    )
    return {
        "report": "swiftchart_v2_forward_strategy_performance",
        "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "scope": "V2 forward rows only; pre-V2 rows with null strategy_version are excluded",
        "formula": "expectancy_r = total completed R / completed opportunities",
        "validation_recommendation_policy": "Performance is measured here, but no automatic validation recommendation or production activation is performed.",
        "overall": _metrics(canonical),
        "breakdown": breakdown,
    }


def strategy_edge_registry_report() -> dict:
    return {
        "registry": "crypto_strategy_edge_registry",
        "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "activation_policy": "Explicit status control only; measured performance never auto-activates a strategy.",
        "entries": edge_registry(),
    }
