from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Iterable

import pandas as pd

from app.config import get_settings
from app.exchanges.factory import get_exchange
from app.models.schemas import TradeIdea
from app.utils.database import get_connection


TIMEFRAME_MINUTES = {
    "30m": 30,
    "1h": 60,
    "2h": 120,
    "4h": 240,
    "6h": 360,
    "8h": 480,
    "12h": 720,
    "1d": 1440,
}


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _parse_dt(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def save_trade_ideas(ideas: Iterable[TradeIdea]) -> list[int]:
    ids: list[int] = []
    with get_connection() as connection:
        for idea in ideas:
            cursor = connection.execute(
                """
                INSERT INTO trade_ideas (
                    symbol, timeframe, exchange, direction, market_regime,
                    higher_timeframe_bias, setup_score, setup_grade,
                    entry_zone_low, entry_zone_high, stop_loss, take_profit_1,
                    take_profit_2, risk_reward, confidence, reason, invalidation
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    idea.symbol.upper(),
                    idea.timeframe,
                    idea.exchange,
                    idea.direction.upper(),
                    idea.market_regime,
                    idea.higher_timeframe_bias,
                    idea.setup_score,
                    idea.setup_grade,
                    idea.entry_zone[0],
                    idea.entry_zone[1],
                    idea.stop_loss,
                    idea.take_profit_1,
                    idea.take_profit_2,
                    idea.risk_reward_ratio,
                    idea.confidence_score,
                    idea.reason,
                    idea.invalid_condition,
                ),
            )
            ids.append(int(cursor.lastrowid))
    return ids


def row_to_dict(row) -> dict:
    data = dict(row)
    for key in ("created_at", "outcome_checked_at", "entry_triggered_at", "closed_at"):
        if data.get(key) and isinstance(data[key], str):
            data[key] = _parse_dt(data[key])
    return data


def list_trade_history(filters: dict) -> list[dict]:
    clauses = []
    values = []
    for field in ("symbol", "timeframe", "status", "result"):
        if filters.get(field):
            clauses.append(f"{field} = ?")
            values.append(str(filters[field]).upper() if field in {"symbol", "status", "result"} else filters[field])
    if filters.get("direction"):
        clauses.append("direction = ?")
        values.append(str(filters["direction"]).upper())
    if filters.get("date_from"):
        clauses.append("created_at >= ?")
        values.append(filters["date_from"])
    if filters.get("date_to"):
        clauses.append("created_at <= ?")
        values.append(filters["date_to"])
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with get_connection() as connection:
        rows = connection.execute(
            f"SELECT * FROM trade_ideas {where} ORDER BY created_at DESC, id DESC LIMIT 500",
            values,
        ).fetchall()
    return [row_to_dict(row) for row in rows]


def get_trade_history(trade_id: int) -> dict | None:
    with get_connection() as connection:
        row = connection.execute("SELECT * FROM trade_ideas WHERE id = ?", (trade_id,)).fetchone()
    return row_to_dict(row) if row else None


def _hit_checks(row: dict, candle: pd.Series) -> tuple[bool, bool, bool]:
    high = float(candle["high"])
    low = float(candle["low"])
    if row["direction"] == "LONG":
        tp1_hit = high >= float(row["take_profit_1"])
        tp2_hit = high >= float(row["take_profit_2"])
        sl_hit = low <= float(row["stop_loss"])
    else:
        tp1_hit = low <= float(row["take_profit_1"])
        tp2_hit = low <= float(row["take_profit_2"])
        sl_hit = high >= float(row["stop_loss"])
    return tp1_hit, tp2_hit, sl_hit


def _entry_triggered(row: dict, candle: pd.Series) -> bool:
    high = float(candle["high"])
    low = float(candle["low"])
    return low <= float(row["entry_zone_high"]) and high >= float(row["entry_zone_low"])


def _expiry_time(row: dict) -> datetime:
    bars = int(getattr(get_settings(), "trade_history_expiry_bars", 12))
    minutes = TIMEFRAME_MINUTES.get(str(row["timeframe"]).lower(), 240)
    return _parse_dt(row["created_at"]) + timedelta(minutes=minutes * bars)


def _r_result(row: dict, status: str) -> tuple[str, float | None]:
    if status == "TP2_HIT":
        return "WIN", float(row["risk_reward"])
    if status == "TP1_HIT":
        return "PARTIAL_WIN", max(0.0, float(row["risk_reward"]) / 2)
    if status == "SL_HIT":
        return "LOSS", -1.0
    if status == "EXPIRED":
        return "NO_ENTRY", 0.0
    if status == "AMBIGUOUS":
        return "AMBIGUOUS", None
    return "OPEN", None


def evaluate_trade(row: dict, candles: pd.DataFrame) -> dict:
    created_at = _parse_dt(row["created_at"])
    later = candles[candles["timestamp"].apply(_parse_dt) > created_at].copy()
    status = row["status"]
    entry_time = row.get("entry_triggered_at")
    closed_at = row.get("closed_at")

    for _, candle in later.iterrows():
        candle_time = _parse_dt(candle["timestamp"])
        if entry_time is None:
            if _entry_triggered(row, candle):
                entry_time = candle_time.isoformat()
                status = "ENTRY_TRIGGERED"
            else:
                continue

        tp1_hit, tp2_hit, sl_hit = _hit_checks(row, candle)
        if sl_hit and (tp1_hit or tp2_hit):
            status = "AMBIGUOUS"
            closed_at = candle_time.isoformat()
            break
        if tp2_hit:
            status = "TP2_HIT"
            closed_at = candle_time.isoformat()
            break
        if tp1_hit:
            status = "TP1_HIT"
            closed_at = candle_time.isoformat()
            break
        if sl_hit:
            status = "SL_HIT"
            closed_at = candle_time.isoformat()
            break

    if entry_time is None and datetime.now(UTC) >= _expiry_time(row):
        status = "EXPIRED"
        closed_at = _now_iso()

    result, pnl = _r_result(row, status)
    return {
        "status": status,
        "result": result,
        "entry_triggered_at": entry_time,
        "closed_at": closed_at,
        "outcome_checked_at": _now_iso(),
        "pnl_r_multiple": pnl,
    }


def update_outcome(trade_id: int, outcome: dict) -> None:
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE trade_ideas
            SET status = ?, result = ?, entry_triggered_at = ?, closed_at = ?,
                outcome_checked_at = ?, pnl_r_multiple = ?
            WHERE id = ?
            """,
            (
                outcome["status"],
                outcome["result"],
                outcome["entry_triggered_at"],
                outcome["closed_at"],
                outcome["outcome_checked_at"],
                outcome["pnl_r_multiple"],
                trade_id,
            ),
        )
        connection.execute(
            """
            INSERT INTO trade_outcomes (
                trade_idea_id, status, result, entry_triggered_at, closed_at,
                outcome_checked_at, pnl_r_multiple, notes
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(trade_idea_id) DO UPDATE SET
                status = excluded.status,
                result = excluded.result,
                entry_triggered_at = excluded.entry_triggered_at,
                closed_at = excluded.closed_at,
                outcome_checked_at = excluded.outcome_checked_at,
                pnl_r_multiple = excluded.pnl_r_multiple,
                notes = excluded.notes
            """,
            (
                trade_id,
                outcome["status"],
                outcome["result"],
                outcome["entry_triggered_at"],
                outcome["closed_at"],
                outcome["outcome_checked_at"],
                outcome["pnl_r_multiple"],
                "Checked from OHLCV candles.",
            ),
        )


async def check_trade_outcomes() -> dict:
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT * FROM trade_ideas
            WHERE status IN ('PENDING', 'ENTRY_TRIGGERED')
            ORDER BY created_at ASC
            """
        ).fetchall()
    checked = 0
    changed = 0
    for raw in rows:
        row = row_to_dict(raw)
        client = get_exchange(row["exchange"])
        candles = await client.get_candles(row["symbol"], row["timeframe"], 1000)
        outcome = evaluate_trade(row, candles)
        checked += 1
        if outcome["status"] != row["status"] or outcome["result"] != row["result"]:
            changed += 1
        update_outcome(row["id"], outcome)
    return {"checked": checked, "updated": changed}


def stats() -> dict:
    with get_connection() as connection:
        rows = [dict(row) for row in connection.execute("SELECT * FROM trade_ideas").fetchall()]
    total = len(rows)
    entry_triggered = [row for row in rows if row.get("entry_triggered_at")]
    wins = [row for row in rows if row["result"] in {"WIN", "PARTIAL_WIN"}]
    losses = [row for row in rows if row["result"] == "LOSS"]
    no_entries = [row for row in rows if row["result"] == "NO_ENTRY"]
    ambiguous = [row for row in rows if row["result"] == "AMBIGUOUS"]
    open_rows = [row for row in rows if row["result"] == "OPEN"]
    tp_hits = [row for row in rows if row["status"] in {"TP1_HIT", "TP2_HIT"}]
    sl_hits = [row for row in rows if row["status"] == "SL_HIT"]
    closed_decided = len(wins) + len(losses)
    r_values = [float(row["pnl_r_multiple"]) for row in rows if row.get("pnl_r_multiple") is not None]

    def grouped(field: str) -> list[dict]:
        buckets: dict[str, list[dict]] = {}
        for row in rows:
            buckets.setdefault(row.get(field) or "Unknown", []).append(row)
        output = []
        for key, items in buckets.items():
            decided = [item for item in items if item["result"] in {"WIN", "PARTIAL_WIN", "LOSS"}]
            item_wins = [item for item in decided if item["result"] in {"WIN", "PARTIAL_WIN"}]
            output.append(
                {
                    field: key,
                    "count": len(items),
                    "win_rate": round((len(item_wins) / len(decided) * 100) if decided else 0, 2),
                    "average_r": round(
                        sum(float(item["pnl_r_multiple"]) for item in items if item.get("pnl_r_multiple") is not None)
                        / max(1, len([item for item in items if item.get("pnl_r_multiple") is not None])),
                        2,
                    ),
                }
            )
        return sorted(output, key=lambda item: (item["win_rate"], item["average_r"], item["count"]), reverse=True)[:5]

    return {
        "total_ideas": total,
        "entry_triggered_count": len(entry_triggered),
        "win_count": len(wins),
        "loss_count": len(losses),
        "no_entry_count": len(no_entries),
        "ambiguous_count": len(ambiguous),
        "open_count": len(open_rows),
        "tp_hit_rate": round((len(tp_hits) / total * 100) if total else 0, 2),
        "sl_hit_rate": round((len(sl_hits) / total * 100) if total else 0, 2),
        "win_rate": round((len(wins) / closed_decided * 100) if closed_decided else 0, 2),
        "average_r_multiple": round(sum(r_values) / len(r_values), 2) if r_values else 0,
        "best_setup_grade_performance": grouped("setup_grade"),
        "best_timeframe_performance": grouped("timeframe"),
        "best_symbol_performance": grouped("symbol"),
    }
