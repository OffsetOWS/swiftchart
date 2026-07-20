from __future__ import annotations

from datetime import UTC, datetime

from app.utils.database import get_connection


COMPLETED_RESULTS = {"WIN", "PARTIAL_WIN", "PARTIAL_LOSS", "LOSS", "BREAK_EVEN"}


def _canonical_rows(rows: list[dict]) -> list[dict]:
    opportunities: dict[str, dict] = {}
    for row in rows:
        key = str(row.get("opportunity_key") or f"legacy-row:{row['id']}")
        existing = opportunities.get(key)
        if existing is None or (str(row.get("created_at") or ""), int(row["id"])) < (
            str(existing.get("created_at") or ""),
            int(existing["id"]),
        ):
            opportunities[key] = row
    return list(opportunities.values())


def _performance(rows: list[dict], retained_percentage: float) -> dict:
    unique_rows = _canonical_rows(rows)
    completed = [
        row
        for row in unique_rows
        if row.get("result") in COMPLETED_RESULTS and row.get("pnl_r_multiple") is not None
    ]
    wins = [row for row in completed if float(row["pnl_r_multiple"]) > 0]
    losses = [row for row in completed if float(row["pnl_r_multiple"]) < 0]
    decided = len(wins) + len(losses)
    total_r = sum(float(row["pnl_r_multiple"]) for row in completed)
    return {
        "unique_opportunities": len(unique_rows),
        "completed_outcomes": len(completed),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / decided * 100, 2) if decided else 0.0,
        "average_winning_r": round(sum(float(row["pnl_r_multiple"]) for row in wins) / len(wins), 4) if wins else 0.0,
        "expectancy_r": round(total_r / len(completed), 4) if completed else 0.0,
        "total_r": round(total_r, 4),
        "retained_percentage": round(retained_percentage, 2),
    }


def strict_trend_short_shadow_report() -> dict:
    """Read-only comparison over instrumented, executable trend-short opportunities."""
    with get_connection() as connection:
        rows = [
            dict(row)
            for row in connection.execute(
                """
                SELECT * FROM trade_ideas
                WHERE market_regime = 'TRENDING_DOWN'
                  AND direction = 'SHORT'
                  AND production_rule_accepted = 1
                  AND strict_trend_short_eligible IS NOT NULL
                  AND COALESCE(entry_status, 'READY') = 'READY'
                  AND (opportunity_key IS NULL OR executable_at IS NOT NULL)
                ORDER BY created_at ASC, id ASC
                """
            ).fetchall()
        ]

    current_rows = _canonical_rows(rows)
    strict_rows = [row for row in current_rows if int(row.get("strict_trend_short_eligible") or 0) == 1]
    retained = len(strict_rows) / len(current_rows) * 100 if current_rows else 0.0
    return {
        "experiment": "strict_trend_short_shadow",
        "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "scope": "instrumented executable TRENDING_DOWN / SHORT opportunities only",
        "current_production_rule": _performance(current_rows, 100.0 if current_rows else 0.0),
        "experimental_strict_rule": _performance(strict_rows, retained),
    }
