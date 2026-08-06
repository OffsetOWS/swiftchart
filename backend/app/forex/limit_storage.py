from __future__ import annotations

from datetime import UTC, datetime
import json

from app.forex.models import ForexLimitOpportunity
from app.utils.database import get_connection


TERMINAL_FILLED_STATUSES = frozenset({"TP1_HIT", "TP2_HIT", "SL_HIT"})
FILLED_STATUSES = frozenset({"ACTIVE_TRADE", "TP1_HIT_TP2_RUNNING", *TERMINAL_FILLED_STATUSES})
CANCELLED_STATUSES = frozenset(
    {"CANCELLED", "INVALIDATED", "NEWS_CANCELLED", "TARGET_REACHED_BEFORE_ENTRY"}
)


def ensure_limit_opportunity_schema() -> None:
    with get_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS forex_limit_opportunities (
                id TEXT PRIMARY KEY,
                pair TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                strategy_family TEXT NOT NULL,
                strategy_version TEXT NOT NULL,
                order_type TEXT NOT NULL CHECK(order_type IN ('BUY_LIMIT','SELL_LIMIT')),
                opportunity_status TEXT NOT NULL,
                direction TEXT NOT NULL,
                market_session TEXT NOT NULL,
                entry_price REAL NOT NULL,
                entry_zone_low REAL NOT NULL,
                entry_zone_high REAL NOT NULL,
                entry_mode TEXT NOT NULL,
                sweep_level REAL NOT NULL,
                sweep_extreme REAL NOT NULL,
                sweep_candle_time TEXT NOT NULL,
                displacement_candle_time TEXT NOT NULL,
                fvg_lower REAL NOT NULL,
                fvg_upper REAL NOT NULL,
                fvg_midpoint REAL NOT NULL,
                fvg_creation_candle_time TEXT NOT NULL,
                stop_loss REAL NOT NULL,
                take_profit_1 REAL NOT NULL,
                take_profit_2 REAL NOT NULL,
                risk_reward_1 REAL NOT NULL,
                risk_reward_2 REAL NOT NULL,
                expiry_time TEXT NOT NULL,
                expiry_candle_count INTEGER NOT NULL,
                fill_time TEXT,
                closed_at TEXT,
                cancellation_reason TEXT,
                invalidation_reason TEXT,
                dxy_context_json TEXT,
                oil_context_json TEXT,
                cross_market_context_json TEXT NOT NULL,
                total_context_adjustment REAL NOT NULL,
                technical_score REAL NOT NULL,
                final_score REAL NOT NULL,
                suggested_position_size REAL,
                mae_pips REAL NOT NULL DEFAULT 0,
                mfe_pips REAL NOT NULL DEFAULT 0,
                pnl_r REAL,
                dedupe_key TEXT NOT NULL UNIQUE,
                shadow_mode INTEGER NOT NULL DEFAULT 1,
                auto_execution_enabled INTEGER NOT NULL DEFAULT 0 CHECK(auto_execution_enabled = 0),
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        existing_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(forex_limit_opportunities)")
        }
        for column, definition in {
            "market_session": "TEXT NOT NULL DEFAULT 'Unknown'",
            "suggested_position_size": "REAL",
            "mae_pips": "REAL NOT NULL DEFAULT 0",
            "mfe_pips": "REAL NOT NULL DEFAULT 0",
            "pnl_r": "REAL",
        }.items():
            if column not in existing_columns:
                connection.execute(
                    f"ALTER TABLE forex_limit_opportunities ADD COLUMN {column} {definition}"
                )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_forex_limit_status_created ON forex_limit_opportunities(opportunity_status, created_at DESC)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_forex_limit_pair_timeframe ON forex_limit_opportunities(pair, timeframe, created_at DESC)"
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS forex_limit_dispatches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                opportunity_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                chat_id TEXT NOT NULL,
                queued_at TEXT NOT NULL,
                attempted_at TEXT,
                delivered_at TEXT,
                retry_count INTEGER NOT NULL DEFAULT 0,
                error_message TEXT,
                UNIQUE(opportunity_id, event_type, chat_id)
            )
            """
        )


def _dump(opportunity: ForexLimitOpportunity) -> str:
    return json.dumps(opportunity.model_dump(mode="json"), separators=(",", ":"), sort_keys=True)


def insert_limit_opportunity(opportunity: ForexLimitOpportunity) -> tuple[ForexLimitOpportunity, bool]:
    ensure_limit_opportunity_schema()
    context = opportunity.context
    with get_connection() as connection:
        existing = connection.execute(
            "SELECT payload_json FROM forex_limit_opportunities WHERE dedupe_key = ?",
            (opportunity.dedupe_key,),
        ).fetchone()
        if existing:
            return ForexLimitOpportunity.model_validate_json(existing["payload_json"]), False
        connection.execute(
            """
            INSERT INTO forex_limit_opportunities (
                id,pair,timeframe,strategy_family,strategy_version,order_type,opportunity_status,direction,market_session,
                entry_price,entry_zone_low,entry_zone_high,entry_mode,sweep_level,sweep_extreme,
                sweep_candle_time,displacement_candle_time,fvg_lower,fvg_upper,fvg_midpoint,
                fvg_creation_candle_time,stop_loss,take_profit_1,take_profit_2,risk_reward_1,
                risk_reward_2,expiry_time,expiry_candle_count,fill_time,closed_at,cancellation_reason,
                invalidation_reason,dxy_context_json,oil_context_json,cross_market_context_json,
                total_context_adjustment,technical_score,final_score,suggested_position_size,
                mae_pips,mfe_pips,pnl_r,dedupe_key,shadow_mode,
                auto_execution_enabled,payload_json,created_at,updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                opportunity.id, opportunity.pair, opportunity.timeframe, opportunity.strategy_family,
                opportunity.strategy_version, opportunity.order_type, opportunity.opportunity_status,
                opportunity.direction, opportunity.market_session, opportunity.entry_price, opportunity.entry_zone_low,
                opportunity.entry_zone_high, opportunity.entry_mode, opportunity.sweep_level,
                opportunity.sweep_extreme, opportunity.sweep_candle_time.isoformat(),
                opportunity.displacement_candle_time.isoformat(), opportunity.fvg.lower,
                opportunity.fvg.upper, opportunity.fvg.midpoint,
                opportunity.fvg.creation_candle_time.isoformat(), opportunity.stop_loss,
                opportunity.take_profit_1, opportunity.take_profit_2, opportunity.risk_reward_1,
                opportunity.risk_reward_2, opportunity.expiry_time.isoformat(),
                opportunity.expiry_candle_count, None, None, None, None,
                json.dumps(context.usd_context.model_dump(mode="json")) if context.usd_context else None,
                json.dumps(context.oil_context.model_dump(mode="json")) if context.oil_context else None,
                json.dumps(context.model_dump(mode="json")), context.total_adjustment,
                opportunity.technical_score, opportunity.final_score,
                opportunity.suggested_position_size, opportunity.mae_pips, opportunity.mfe_pips,
                opportunity.pnl_r, opportunity.dedupe_key,
                int(opportunity.shadow_mode), 0, _dump(opportunity),
                opportunity.created_at.isoformat(), opportunity.updated_at.isoformat(),
            ),
        )
    return opportunity, True


def update_limit_opportunity(opportunity: ForexLimitOpportunity) -> ForexLimitOpportunity:
    ensure_limit_opportunity_schema()
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE forex_limit_opportunities
            SET opportunity_status=?, fill_time=?, closed_at=?, cancellation_reason=?,
                invalidation_reason=?, mae_pips=?, mfe_pips=?, pnl_r=?, payload_json=?, updated_at=?
            WHERE id=?
            """,
            (
                opportunity.opportunity_status,
                opportunity.fill_time.isoformat() if opportunity.fill_time else None,
                opportunity.closed_at.isoformat() if opportunity.closed_at else None,
                opportunity.cancellation_reason, opportunity.invalidation_reason,
                opportunity.mae_pips, opportunity.mfe_pips, opportunity.pnl_r,
                _dump(opportunity), opportunity.updated_at.isoformat(), opportunity.id,
            ),
        )
    return opportunity


def list_limit_opportunities(
    statuses: tuple[str, ...] | None = None,
    *,
    limit: int = 100,
    include_shadow: bool = True,
) -> list[ForexLimitOpportunity]:
    ensure_limit_opportunity_schema()
    query = "SELECT payload_json FROM forex_limit_opportunities"
    values: list[object] = []
    conditions: list[str] = []
    if statuses:
        conditions.append(f"opportunity_status IN ({','.join('?' for _ in statuses)})")
        values.extend(statuses)
    if not include_shadow:
        conditions.append("shadow_mode = 0")
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY created_at DESC LIMIT ?"
    values.append(max(1, min(500, limit)))
    with get_connection() as connection:
        rows = connection.execute(query, values).fetchall()
    return [ForexLimitOpportunity.model_validate_json(row["payload_json"]) for row in rows]


def get_limit_opportunity(opportunity_id: str) -> ForexLimitOpportunity | None:
    ensure_limit_opportunity_schema()
    with get_connection() as connection:
        row = connection.execute(
            "SELECT payload_json FROM forex_limit_opportunities WHERE id=?", (opportunity_id,)
        ).fetchone()
    return ForexLimitOpportunity.model_validate_json(row["payload_json"]) if row else None


def limit_strategy_stats(*, include_shadow: bool = True) -> dict:
    opportunities = list_limit_opportunities(limit=500, include_shadow=include_shadow)
    counts: dict[str, int] = {}
    for opportunity in opportunities:
        counts[opportunity.opportunity_status] = counts.get(opportunity.opportunity_status, 0) + 1
    total = len(opportunities)
    filled_items = [item for item in opportunities if item.opportunity_status in FILLED_STATUSES]
    filled = len(filled_items)
    resolved_fills = [item for item in filled_items if item.opportunity_status in TERMINAL_FILLED_STATUSES]
    pending = counts.get("WAIT_FOR_RETEST", 0) + counts.get("PENDING_LIMIT", 0)
    expired = counts.get("EXPIRED", 0) + counts.get("MISSED_NO_RETEST", 0)
    cancelled = sum(counts.get(key, 0) for key in CANCELLED_STATUSES)

    def rate(numerator: int, denominator: int = total) -> float:
        return round(numerator / denominator * 100, 1) if denominator else 0.0

    def average(values: list[float]) -> float:
        return round(sum(values) / len(values), 2) if values else 0.0

    def breakdown(field: str) -> dict[str, dict[str, float | int]]:
        result: dict[str, dict[str, float | int]] = {}
        for item in opportunities:
            key = str(getattr(item, field) or "Unknown")
            bucket = result.setdefault(key, {"detected": 0, "filled": 0, "resolved": 0, "pnl_r": 0.0})
            bucket["detected"] += 1
            if item in filled_items:
                bucket["filled"] += 1
            if item.pnl_r is not None:
                bucket["resolved"] += 1
                bucket["pnl_r"] += item.pnl_r
        for bucket in result.values():
            bucket["fill_rate"] = rate(int(bucket["filled"]), int(bucket["detected"]))
            bucket["expectancy_r"] = (
                round(float(bucket.pop("pnl_r")) / int(bucket["resolved"]), 2)
                if bucket["resolved"] else 0.0
            )
        return result

    fill_seconds = [
        (item.fill_time - item.created_at).total_seconds()
        for item in filled_items
        if item.fill_time is not None
    ]
    resolved_pnl = [item.pnl_r for item in resolved_fills if item.pnl_r is not None]
    all_pnl = [item.pnl_r or 0.0 for item in opportunities]

    def context_split(component_name: str) -> dict[str, dict[str, float | int]]:
        result = {"alignment": {"count": 0, "average_pnl_r": 0.0}, "conflict": {"count": 0, "average_pnl_r": 0.0}}
        pnl: dict[str, list[float]] = {"alignment": [], "conflict": []}
        for item in opportunities:
            component = getattr(item.context, component_name)
            if component is None:
                continue
            group = (
                "alignment" if component.alignment_status in {"ALIGNED", "STRONG_ALIGNMENT"}
                else "conflict" if component.alignment_status in {"CONFLICT", "STRONG_CONFLICT"}
                else None
            )
            if group:
                result[group]["count"] += 1
                if item.pnl_r is not None:
                    pnl[group].append(item.pnl_r)
        for group in result:
            result[group]["average_pnl_r"] = average(pnl[group])
        return result

    return {
        "strategy_id": "liquidity_sweep_fvg_limit_v1",
        "total_detected": total,
        "pending_limits": pending,
        "filled": filled,
        "fill_rate": rate(filled),
        "missed_no_retest_rate": rate(counts.get("MISSED_NO_RETEST", 0)),
        "expiry_rate": rate(expired),
        "cancellation_rate": rate(cancelled),
        "win_rate_after_fill": rate(counts.get("TP2_HIT", 0), len(resolved_fills)),
        "expectancy_after_fill_r": average(resolved_pnl),
        "expectancy_including_unfilled_r": average(all_pnl),
        "average_time_to_fill_seconds": average(fill_seconds),
        "average_time_to_fill_hours": round(average(fill_seconds) / 3600, 2),
        "average_mae_pips": average([item.mae_pips for item in filled_items]),
        "average_mfe_pips": average([item.mfe_pips for item in filled_items]),
        "tp1_rate": rate(counts.get("TP1_HIT", 0) + counts.get("TP1_HIT_TP2_RUNNING", 0) + counts.get("TP2_HIT", 0), filled),
        "tp2_rate": rate(counts.get("TP2_HIT", 0), filled),
        "sl_rate": rate(counts.get("SL_HIT", 0), filled),
        "expired": expired,
        "cancelled": cancelled,
        "status_counts": counts,
        "by_pair": breakdown("pair"),
        "by_timeframe": breakdown("timeframe"),
        "by_session": breakdown("market_session"),
        "by_entry_mode": breakdown("entry_mode"),
        "dxy_context": context_split("usd_context"),
        "oil_context": context_split("oil_context"),
    }


def queue_limit_dispatches(opportunity_id: str, event_type: str, chat_ids: list[str]) -> int:
    ensure_limit_opportunity_schema()
    now = datetime.now(UTC).isoformat()
    with get_connection() as connection:
        before = connection.total_changes
        connection.executemany(
            """
            INSERT OR IGNORE INTO forex_limit_dispatches
                (opportunity_id,event_type,chat_id,queued_at)
            VALUES (?,?,?,?)
            """,
            [(opportunity_id, event_type, str(chat_id), now) for chat_id in chat_ids],
        )
        return connection.total_changes - before


def claim_limit_dispatches(limit: int = 100) -> list[dict]:
    ensure_limit_opportunity_schema()
    now = datetime.now(UTC).isoformat()
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT * FROM forex_limit_dispatches
            WHERE delivered_at IS NULL AND retry_count < 5
            ORDER BY queued_at, id LIMIT ?
            """,
            (limit,),
        ).fetchall()
        for row in rows:
            connection.execute(
                "UPDATE forex_limit_dispatches SET attempted_at=?, retry_count=retry_count+1 WHERE id=?",
                (now, row["id"]),
            )
    return [dict(row) for row in rows]


def mark_limit_dispatch(dispatch_id: int, *, delivered: bool, error: str | None = None) -> None:
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE forex_limit_dispatches
            SET delivered_at=CASE WHEN ? THEN ? ELSE delivered_at END, error_message=?
            WHERE id=?
            """,
            (int(delivered), datetime.now(UTC).isoformat(), error, dispatch_id),
        )
