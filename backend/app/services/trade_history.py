from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
import logging
from typing import Iterable

import pandas as pd

from app.config import get_settings
from app.models.schemas import SignalReview, TradeIdea
from app.services.alert_dedupe import mark_alert_sent, setup_fingerprint, should_skip_alert
from app.services.market_data import get_candles_cached
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

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _sqlite_cutoff(minutes: int = 30) -> str:
    return (datetime.now(UTC) - timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M:%S")


def _parse_dt(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _timeframe_minutes(timeframe: str) -> int:
    return TIMEFRAME_MINUTES.get(str(timeframe).lower(), 240)


def _market_structure_changed_after_sl(row: dict, idea: TradeIdea) -> bool:
    previous_score = row.get("regime_score")
    current_score = idea.regime_score
    if previous_score is not None and current_score is not None and abs(float(current_score) - float(previous_score)) >= 30:
        return True
    if row.get("regime_label") and idea.regime_label and str(row["regime_label"]) != str(idea.regime_label):
        return True
    if idea.trend_alignment == "with-trend" and row.get("trend_alignment") != "with-trend":
        return True
    if (idea.setup_score or idea.confidence_score) >= 88 and len(idea.reversal_confirmations) >= 3:
        return True
    return False


def same_direction_sl_cooldown_review(idea: TradeIdea, *, candles: int = 6, now: datetime | None = None) -> SignalReview | None:
    signal_time = idea.signal_candle_time or now or datetime.now(UTC)
    signal_time = signal_time if signal_time.tzinfo else signal_time.replace(tzinfo=UTC)
    cutoff = signal_time - timedelta(minutes=_timeframe_minutes(idea.timeframe) * candles)
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT * FROM trade_ideas
            WHERE symbol = ?
              AND timeframe = ?
              AND exchange = ?
              AND direction = ?
              AND (status = 'SL_HIT' OR sl_hit_at IS NOT NULL)
              AND COALESCE(sl_hit_at, closed_at, outcome_checked_at, created_at) >= ?
            ORDER BY COALESCE(sl_hit_at, closed_at, outcome_checked_at, created_at) DESC, id DESC
            LIMIT 1
            """,
            (
                idea.symbol.upper(),
                idea.timeframe,
                idea.exchange,
                idea.direction.upper(),
                cutoff.isoformat(),
            ),
        ).fetchone()
    if row is None:
        return None
    previous = dict(row)
    if _market_structure_changed_after_sl(previous, idea):
        logger.info(
            "SL cooldown bypassed pair=%s direction=%s bias=%s confidence=%.1f reason=market_structure_changed previous_regime=%s current_regime=%s",
            idea.symbol,
            idea.direction,
            idea.regime_bias,
            idea.confidence_score,
            previous.get("regime_label"),
            idea.regime_label,
        )
        return None
    reason = (
        f"{idea.direction} signal rejected because {idea.symbol.upper()} hit SL on the same direction "
        f"within the last {candles} {idea.timeframe} candles and market structure has not changed."
    )
    logger.info(
        "Signal rejected pair=%s direction=%s bias=%s btc_regime=%s confidence=%.1f rejection_reason=%s",
        idea.symbol,
        idea.direction,
        idea.regime_bias or "-",
        "n/a",
        idea.confidence_score,
        reason,
    )
    return SignalReview(
        symbol=idea.symbol,
        timeframe=idea.timeframe,
        exchange=idea.exchange,
        direction=idea.direction,
        accepted=False,
        reason=reason,
        base_score=idea.setup_score,
        adjusted_score=max(0.0, (idea.setup_score or idea.confidence_score) - 60),
        confidence_adjustment=-60,
        regime_score=idea.regime_score or 0,
        regime_label=idea.regime_label or "Unknown",
        trend_alignment=idea.trend_alignment or "range-trade",
        reversal_confirmations=idea.reversal_confirmations,
    )


def _signal_expires_at(idea: TradeIdea) -> str:
    candle_time = idea.signal_candle_time
    if candle_time is None:
        return (datetime.now(UTC) + timedelta(minutes=get_settings().signal_max_age_minutes)).replace(microsecond=0).isoformat()
    normalized = candle_time if candle_time.tzinfo else candle_time.replace(tzinfo=UTC)
    return (normalized + timedelta(minutes=get_settings().signal_max_age_minutes)).replace(microsecond=0).isoformat()


def _recent_duplicate_id(connection, idea: TradeIdea) -> int | None:
    row = connection.execute(
        """
        SELECT id FROM trade_ideas
        WHERE symbol = ?
          AND timeframe = ?
          AND exchange = ?
          AND direction = ?
          AND ABS(entry_zone_low - ?) < 0.00000001
          AND ABS(entry_zone_high - ?) < 0.00000001
          AND ABS(stop_loss - ?) < 0.00000001
          AND ABS(take_profit_1 - ?) < 0.00000001
          AND ABS(take_profit_2 - ?) < 0.00000001
          AND created_at >= ?
        ORDER BY created_at DESC, id DESC
        LIMIT 1
        """,
        (
            idea.symbol.upper(),
            idea.timeframe,
            idea.exchange,
            idea.direction.upper(),
            idea.entry_zone[0],
            idea.entry_zone[1],
            idea.stop_loss,
            idea.take_profit_1,
            idea.take_profit_2,
            _sqlite_cutoff(30),
        ),
    ).fetchone()
    return int(row["id"]) if row else None


def save_trade_ideas(ideas: Iterable[TradeIdea]) -> list[int]:
    ids: list[int] = []
    with get_connection() as connection:
        for idea in ideas:
            try:
                if should_skip_alert(idea, namespace="history"):
                    continue
                duplicate_id = _recent_duplicate_id(connection, idea)
                if duplicate_id is not None:
                    logger.info("Skipped duplicate trade idea id=%s symbol=%s timeframe=%s exchange=%s", duplicate_id, idea.symbol, idea.timeframe, idea.exchange)
                    continue
                cursor = connection.execute(
                    """
                    INSERT INTO trade_ideas (
                        symbol, timeframe, exchange, direction, market_regime,
                        higher_timeframe_bias, setup_score, setup_grade,
                        entry_zone_low, entry_zone_high, stop_loss, take_profit_1,
                        take_profit_2, risk_reward, confidence, reason, invalidation,
                        regime_score, regime_label, trend_alignment, regime_confidence_adjustment,
                        reversal_confirmations, regime_explanation, signal_candle_time,
                        signal_fingerprint, lifecycle_status, expires_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        idea.regime_score,
                        idea.regime_label,
                        idea.trend_alignment,
                        idea.regime_confidence_adjustment,
                        json.dumps(idea.reversal_confirmations),
                        idea.regime_explanation,
                        idea.signal_candle_time.isoformat() if idea.signal_candle_time else None,
                        setup_fingerprint(idea),
                        "active",
                        _signal_expires_at(idea),
                    ),
                )
                trade_id = int(cursor.lastrowid)
                connection.execute(
                    """
                    INSERT INTO signal_reviews (
                        symbol, timeframe, exchange, direction, accepted, reason,
                        base_score, adjusted_score, confidence_adjustment,
                        regime_score, regime_label, trend_alignment, reversal_confirmations
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        idea.symbol.upper(),
                        idea.timeframe,
                        idea.exchange,
                        idea.direction.upper(),
                        1,
                        "Signal accepted and saved as a trade idea.",
                        None,
                        idea.confidence_score,
                        idea.regime_confidence_adjustment,
                        idea.regime_score or 0,
                        idea.regime_label or "Ranging / Neutral",
                        idea.trend_alignment or "range-trade",
                        json.dumps(idea.reversal_confirmations),
                    ),
                )
                ids.append(trade_id)
                mark_alert_sent(idea, namespace="history")
                logger.info("Saved trade idea id=%s symbol=%s timeframe=%s exchange=%s direction=%s", trade_id, idea.symbol, idea.timeframe, idea.exchange, idea.direction)
            except Exception:
                logger.exception("Failed to save trade idea symbol=%s timeframe=%s exchange=%s", idea.symbol, idea.timeframe, idea.exchange)
    return ids


def save_signal_reviews(reviews: Iterable[SignalReview]) -> int:
    count = 0
    with get_connection() as connection:
        for review in reviews:
            try:
                connection.execute(
                    """
                    INSERT INTO signal_reviews (
                        symbol, timeframe, exchange, direction, accepted, reason,
                        base_score, adjusted_score, confidence_adjustment,
                        regime_score, regime_label, trend_alignment, reversal_confirmations
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        review.symbol.upper(),
                        review.timeframe,
                        review.exchange,
                        review.direction.upper(),
                        1 if review.accepted else 0,
                        review.reason,
                        review.base_score,
                        review.adjusted_score,
                        review.confidence_adjustment,
                        review.regime_score,
                        review.regime_label,
                        review.trend_alignment,
                        json.dumps(review.reversal_confirmations),
                    ),
                )
                count += 1
            except Exception:
                logger.exception("Failed to save signal review symbol=%s timeframe=%s exchange=%s", review.symbol, review.timeframe, review.exchange)
    return count


def row_to_dict(row) -> dict:
    data = dict(row)
    for key in ("created_at", "outcome_checked_at", "entry_triggered_at", "tp1_hit_at", "tp2_hit_at", "sl_hit_at", "expired_at", "closed_at"):
        if data.get(key) and isinstance(data[key], str):
            data[key] = _parse_dt(data[key])
    return data


def _history_where(filters: dict) -> tuple[str, list]:
    clauses = []
    values = []
    for field in ("symbol", "timeframe", "exchange", "status", "result"):
        if filters.get(field):
            clauses.append(f"{field} = ?")
            values.append(str(filters[field]).upper() if field in {"symbol", "status", "result"} else str(filters[field]).lower() if field == "exchange" else filters[field])
    if filters.get("direction"):
        clauses.append("direction = ?")
        values.append(str(filters["direction"]).upper())
    if filters.get("date_from"):
        clauses.append("created_at >= ?")
        values.append(filters["date_from"])
    if filters.get("date_to"):
        clauses.append("created_at <= ?")
        date_to = str(filters["date_to"])
        values.append(f"{date_to} 23:59:59" if len(date_to) == 10 else date_to)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return where, values


def query_trade_history(filters: dict, page: int = 1, limit: int = 20, sort: str = "desc") -> dict:
    page = max(1, int(page or 1))
    limit = min(250, max(1, int(limit or 20)))
    direction = "ASC" if str(sort).lower() == "asc" else "DESC"
    where, values = _history_where(filters)
    offset = (page - 1) * limit
    with get_connection() as connection:
        total = int(connection.execute(f"SELECT COUNT(*) AS count FROM trade_ideas {where}", values).fetchone()["count"])
        rows = connection.execute(
            f"SELECT * FROM trade_ideas {where} ORDER BY created_at {direction}, id {direction} LIMIT ? OFFSET ?",
            [*values, limit, offset],
        ).fetchall()
    if total == 0:
        logger.info("No trade history records found for filters=%s", {key: value for key, value in filters.items() if value})
    return {
        "records": [row_to_dict(row) for row in rows],
        "page": page,
        "limit": limit,
        "total": total,
        "pages": (total + limit - 1) // limit if total else 0,
        "sort": "asc" if direction == "ASC" else "desc",
    }


def list_trade_history(filters: dict, page: int = 1, limit: int = 20, sort: str = "desc") -> list[dict]:
    return query_trade_history(filters, page=page, limit=limit, sort=sort)["records"]


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


def _entry_price(row: dict) -> float:
    return (float(row["entry_zone_low"]) + float(row["entry_zone_high"])) / 2


def _tp1_r(row: dict) -> float:
    risk = abs(_entry_price(row) - float(row["stop_loss"]))
    if risk <= 0:
        return 0.0
    return abs(float(row["take_profit_1"]) - _entry_price(row)) / risk


def _partial_r(row: dict, runner_r: float) -> float:
    return (_tp1_r(row) * 0.5) + (runner_r * 0.5)


def _result_for_r(value: float) -> str:
    if value > 0:
        return "PARTIAL_WIN"
    if value < 0:
        return "PARTIAL_LOSS"
    return "BREAK_EVEN"


def _r_result(row: dict, status: str) -> tuple[str, float | None]:
    if status == "TP2_HIT":
        return "WIN", float(row["risk_reward"])
    if status == "BREAK_EVEN":
        return "BREAK_EVEN", round(_partial_r(row, 0.0), 4)
    if status == "CLOSED_AFTER_TP1":
        pnl = round(_partial_r(row, -1.0), 4)
        return _result_for_r(pnl), pnl
    if status == "PARTIAL_WIN":
        return "PARTIAL_WIN", round(_partial_r(row, 0.0), 4)
    if status == "PARTIAL_LOSS":
        return "PARTIAL_LOSS", round(_partial_r(row, -1.0), 4)
    if status == "TP1_HIT":
        return "OPEN", None
    if status == "SL_HIT":
        return "LOSS", -1.0
    if status == "EXPIRED":
        return "NO_ENTRY", 0.0
    if status == "AMBIGUOUS":
        return "AMBIGUOUS", None
    return "OPEN", None


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _stop_hit(row: dict, candle: pd.Series, stop_price: float) -> bool:
    high = float(candle["high"])
    low = float(candle["low"])
    return low <= stop_price if row["direction"] == "LONG" else high >= stop_price


def evaluate_trade(row: dict, candles: pd.DataFrame, *, expiry_bars: int | None = None, move_stop_to_entry_after_tp1: bool | None = None) -> dict:
    created_at = _parse_dt(row["created_at"])
    later = candles[candles["timestamp"].apply(_parse_dt) > created_at].copy().sort_values("timestamp")
    max_bars = int(expiry_bars or getattr(get_settings(), "trade_history_expiry_bars", 12))
    move_to_be = bool(get_settings().trade_history_move_stop_to_entry_after_tp1 if move_stop_to_entry_after_tp1 is None else move_stop_to_entry_after_tp1)
    status = "OPEN"
    entry_time: datetime | None = None
    tp1_time: datetime | None = None
    tp2_time: datetime | None = None
    sl_time: datetime | None = None
    expired_time: datetime | None = None
    closed_at: datetime | None = None
    candles_to_resolution: int | None = None
    lifecycle_events: list[dict] = []

    for candle_index, (_, candle) in enumerate(later.iterrows(), start=1):
        candle_time = _parse_dt(candle["timestamp"])
        if entry_time is None:
            if _entry_triggered(row, candle):
                entry_time = candle_time
                status = "ENTRY_TRIGGERED"
                lifecycle_events.append({"event": "ENTRY_TRIGGERED", "at": candle_time.isoformat(), "candle": candle_index})
            else:
                if candle_index >= max_bars:
                    status = "EXPIRED"
                    expired_time = candle_time
                    closed_at = candle_time
                    candles_to_resolution = candle_index
                    lifecycle_events.append({"event": "EXPIRED", "at": candle_time.isoformat(), "candle": candle_index})
                    break
                continue

        tp1_hit, tp2_hit, sl_hit = _hit_checks(row, candle)

        if tp1_time is None:
            if sl_hit and (tp1_hit or tp2_hit):
                status = "AMBIGUOUS"
                sl_time = candle_time
                closed_at = candle_time
                candles_to_resolution = candle_index
                lifecycle_events.append({"event": "AMBIGUOUS", "at": candle_time.isoformat(), "candle": candle_index})
                break
            if tp2_hit:
                tp1_time = candle_time
                tp2_time = candle_time
                status = "TP2_HIT"
                closed_at = candle_time
                candles_to_resolution = candle_index
                lifecycle_events.append({"event": "TP1_HIT", "at": candle_time.isoformat(), "candle": candle_index})
                lifecycle_events.append({"event": "TP2_HIT", "at": candle_time.isoformat(), "candle": candle_index})
                break
            if tp1_hit:
                tp1_time = candle_time
                status = "TP1_HIT"
                lifecycle_events.append({"event": "TP1_HIT", "at": candle_time.isoformat(), "candle": candle_index})
                if candle_index >= max_bars:
                    status = "PARTIAL_WIN"
                    expired_time = candle_time
                    closed_at = candle_time
                    candles_to_resolution = candle_index
                    lifecycle_events.append({"event": "EXPIRED_AFTER_TP1", "at": candle_time.isoformat(), "candle": candle_index})
                    break
                continue
            if sl_hit:
                status = "SL_HIT"
                sl_time = candle_time
                closed_at = candle_time
                candles_to_resolution = candle_index
                lifecycle_events.append({"event": "SL_HIT", "at": candle_time.isoformat(), "candle": candle_index})
                break
        else:
            active_stop = _entry_price(row) if move_to_be else float(row["stop_loss"])
            runner_stop_hit = _stop_hit(row, candle, active_stop)
            if runner_stop_hit and tp2_hit:
                status = "AMBIGUOUS"
                sl_time = candle_time
                closed_at = candle_time
                candles_to_resolution = candle_index
                lifecycle_events.append({"event": "AMBIGUOUS_AFTER_TP1", "at": candle_time.isoformat(), "candle": candle_index})
                break
            if tp2_hit:
                tp2_time = candle_time
                status = "TP2_HIT"
                closed_at = candle_time
                candles_to_resolution = candle_index
                lifecycle_events.append({"event": "TP2_HIT", "at": candle_time.isoformat(), "candle": candle_index})
                break
            if runner_stop_hit:
                sl_time = candle_time
                status = "BREAK_EVEN" if move_to_be else "CLOSED_AFTER_TP1"
                closed_at = candle_time
                candles_to_resolution = candle_index
                lifecycle_events.append({"event": status, "at": candle_time.isoformat(), "candle": candle_index})
                break

        if candle_index >= max_bars:
            expired_time = candle_time
            closed_at = candle_time
            candles_to_resolution = candle_index
            if tp1_time is None:
                status = "EXPIRED"
                lifecycle_events.append({"event": "EXPIRED", "at": candle_time.isoformat(), "candle": candle_index})
            else:
                status = "PARTIAL_WIN"
                lifecycle_events.append({"event": "EXPIRED_AFTER_TP1", "at": candle_time.isoformat(), "candle": candle_index})
            break

    if not later.empty and closed_at is None and len(later) >= max_bars:
        candle = later.iloc[max_bars - 1]
        candle_time = _parse_dt(candle["timestamp"])
        expired_time = candle_time
        closed_at = candle_time
        candles_to_resolution = max_bars
        if tp1_time is None:
            status = "EXPIRED"
            lifecycle_events.append({"event": "EXPIRED", "at": candle_time.isoformat(), "candle": max_bars})
        else:
            status = "PARTIAL_WIN"
            lifecycle_events.append({"event": "EXPIRED_AFTER_TP1", "at": candle_time.isoformat(), "candle": max_bars})

    if closed_at is None and entry_time is not None:
        status = "TP1_HIT" if tp1_time is not None else "OPEN"
    elif closed_at is None:
        status = "OPEN"

    result, pnl = _r_result(row, status)
    return {
        "status": status,
        "result": result,
        "entry_triggered_at": _iso(entry_time),
        "tp1_hit_at": _iso(tp1_time),
        "tp2_hit_at": _iso(tp2_time),
        "sl_hit_at": _iso(sl_time),
        "expired_at": _iso(expired_time),
        "closed_at": _iso(closed_at),
        "outcome_checked_at": _now_iso(),
        "pnl_r_multiple": pnl,
        "candles_to_resolution": candles_to_resolution,
        "lifecycle_events": json.dumps(lifecycle_events),
    }


def update_outcome(trade_id: int, outcome: dict) -> None:
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE trade_ideas
            SET status = ?, result = ?, entry_triggered_at = ?, tp1_hit_at = ?,
                tp2_hit_at = ?, sl_hit_at = ?, expired_at = ?, closed_at = ?,
                outcome_checked_at = ?, pnl_r_multiple = ?, candles_to_resolution = ?,
                lifecycle_events = ?
            WHERE id = ?
            """,
            (
                outcome["status"],
                outcome["result"],
                outcome["entry_triggered_at"],
                outcome["tp1_hit_at"],
                outcome["tp2_hit_at"],
                outcome["sl_hit_at"],
                outcome["expired_at"],
                outcome["closed_at"],
                outcome["outcome_checked_at"],
                outcome["pnl_r_multiple"],
                outcome["candles_to_resolution"],
                outcome["lifecycle_events"],
                trade_id,
            ),
        )
        connection.execute(
            """
            INSERT INTO trade_outcomes (
                trade_idea_id, status, result, entry_triggered_at, tp1_hit_at,
                tp2_hit_at, sl_hit_at, expired_at, closed_at, outcome_checked_at,
                pnl_r_multiple, candles_to_resolution, lifecycle_events, notes
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(trade_idea_id) DO UPDATE SET
                status = excluded.status,
                result = excluded.result,
                entry_triggered_at = excluded.entry_triggered_at,
                tp1_hit_at = excluded.tp1_hit_at,
                tp2_hit_at = excluded.tp2_hit_at,
                sl_hit_at = excluded.sl_hit_at,
                expired_at = excluded.expired_at,
                closed_at = excluded.closed_at,
                outcome_checked_at = excluded.outcome_checked_at,
                pnl_r_multiple = excluded.pnl_r_multiple,
                candles_to_resolution = excluded.candles_to_resolution,
                lifecycle_events = excluded.lifecycle_events,
                notes = excluded.notes
            """,
            (
                trade_id,
                outcome["status"],
                outcome["result"],
                outcome["entry_triggered_at"],
                outcome["tp1_hit_at"],
                outcome["tp2_hit_at"],
                outcome["sl_hit_at"],
                outcome["expired_at"],
                outcome["closed_at"],
                outcome["outcome_checked_at"],
                outcome["pnl_r_multiple"],
                outcome["candles_to_resolution"],
                outcome["lifecycle_events"],
                "Checked from OHLCV candles.",
            ),
        )


ACTIVE_TRADE_STATUSES = ("PENDING", "OPEN", "ENTRY_TRIGGERED", "TP1_HIT")


async def _evaluate_rows(rows) -> dict:
    checked = 0
    changed = 0
    errors = 0
    candle_cache: dict[tuple[str, str, str], pd.DataFrame] = {}
    for raw in rows:
        row = row_to_dict(raw)
        try:
            cache_key = (row["exchange"], row["symbol"], row["timeframe"])
            if cache_key not in candle_cache:
                candle_cache[cache_key] = await get_candles_cached(row["exchange"], row["symbol"], row["timeframe"], 1000)
            candles = candle_cache[cache_key]
            outcome = evaluate_trade(row, candles)
        except Exception:
            errors += 1
            logger.exception("Trade lifecycle evaluation failed id=%s symbol=%s timeframe=%s exchange=%s", row.get("id"), row.get("symbol"), row.get("timeframe"), row.get("exchange"))
            continue
        checked += 1
        if outcome["status"] != row["status"] or outcome["result"] != row["result"]:
            changed += 1
        update_outcome(row["id"], outcome)
    return {"checked": checked, "updated": changed, "errors": errors, "markets_fetched": len(candle_cache)}


async def check_trade_outcomes() -> dict:
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT * FROM trade_ideas
            WHERE status IN ('PENDING', 'OPEN', 'ENTRY_TRIGGERED', 'TP1_HIT')
            ORDER BY created_at ASC
            """
        ).fetchall()
    return await _evaluate_rows(rows)


async def replay_trade_outcomes(days: int = 30) -> dict:
    cutoff = (datetime.now(UTC) - timedelta(days=max(1, int(days)))).replace(microsecond=0).isoformat()
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT * FROM trade_ideas
            WHERE created_at >= ?
            ORDER BY created_at ASC
            """,
            (cutoff,),
        ).fetchall()
    result = await _evaluate_rows(rows)
    result["days"] = max(1, int(days))
    result["since"] = cutoff
    return result


def stats() -> dict:
    with get_connection() as connection:
        rows = [dict(row) for row in connection.execute("SELECT * FROM trade_ideas").fetchall()]
        review_rows = [dict(row) for row in connection.execute("SELECT * FROM signal_reviews").fetchall()]
    total = len(rows)
    entry_triggered = [row for row in rows if row.get("entry_triggered_at")]
    wins = [row for row in rows if row["result"] in {"WIN", "PARTIAL_WIN"}]
    losses = [row for row in rows if row["result"] in {"LOSS", "PARTIAL_LOSS"}]
    no_entries = [row for row in rows if row["result"] == "NO_ENTRY"]
    break_evens = [row for row in rows if row["result"] == "BREAK_EVEN"]
    ambiguous = [row for row in rows if row["result"] == "AMBIGUOUS"]
    open_rows = [row for row in rows if row["result"] == "OPEN"]
    tp_hits = [row for row in rows if row.get("tp1_hit_at") or row["status"] in {"TP1_HIT", "TP2_HIT", "CLOSED_AFTER_TP1", "BREAK_EVEN", "PARTIAL_WIN", "PARTIAL_LOSS"}]
    sl_hits = [row for row in rows if row.get("sl_hit_at") or row["status"] == "SL_HIT"]
    closed_decided = len(wins) + len(losses)
    r_values = [float(row["pnl_r_multiple"]) for row in rows if row.get("pnl_r_multiple") is not None]
    resolved_candles = [int(row["candles_to_resolution"]) for row in rows if row.get("candles_to_resolution") is not None]

    def event_contains(row: dict, event: str) -> bool:
        return event in str(row.get("lifecycle_events") or "")

    tp1_then_sl = [row for row in rows if row.get("tp1_hit_at") and row.get("sl_hit_at") and not row.get("tp2_hit_at")]
    tp1_then_tp2 = [row for row in rows if row.get("tp1_hit_at") and row.get("tp2_hit_at")]
    tp1_then_expiry = [row for row in rows if row.get("tp1_hit_at") and row.get("expired_at") and event_contains(row, "EXPIRED_AFTER_TP1")]

    def grouped(field: str) -> list[dict]:
        buckets: dict[str, list[dict]] = {}
        for row in rows:
            buckets.setdefault(row.get(field) or "Unknown", []).append(row)
        output = []
        for key, items in buckets.items():
            decided = [item for item in items if item["result"] in {"WIN", "PARTIAL_WIN", "PARTIAL_LOSS", "LOSS", "BREAK_EVEN"}]
            item_wins = [item for item in decided if item["result"] in {"WIN", "PARTIAL_WIN"}]
            item_r_values = [float(item["pnl_r_multiple"]) for item in items if item.get("pnl_r_multiple") is not None]
            output.append(
                {
                    field: key,
                    "count": len(items),
                    "win_rate": round((len(item_wins) / len(decided) * 100) if decided else 0, 2),
                    "average_r": round(sum(item_r_values) / len(item_r_values), 2) if item_r_values else 0,
                    "expectancy": round(sum(item_r_values) / len(items), 2) if items else 0,
                }
            )
        return sorted(output, key=lambda item: (item["expectancy"], item["win_rate"], item["count"]), reverse=True)[:5]

    def review_grouped() -> list[dict]:
        buckets: dict[str, list[dict]] = {"accepted": [], "rejected": []}
        for row in review_rows:
            buckets["accepted" if int(row.get("accepted") or 0) else "rejected"].append(row)
        return [
            {
                "status": key,
                "count": len(items),
                "average_base_score": round(sum(float(item["base_score"] or 0) for item in items) / len(items), 2) if items else 0,
                "average_adjusted_score": round(sum(float(item["adjusted_score"] or 0) for item in items) / len(items), 2) if items else 0,
            }
            for key, items in buckets.items()
        ]

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
        "average_candles_to_resolution": round(sum(resolved_candles) / len(resolved_candles), 2) if resolved_candles else 0,
        "expectancy_per_trade": round(sum(r_values) / total, 2) if total else 0,
        "tp1_then_sl_count": len(tp1_then_sl),
        "tp1_then_tp2_count": len(tp1_then_tp2),
        "tp1_then_expiry_count": len(tp1_then_expiry),
        "break_even_count": len(break_evens),
        "partial_loss_count": len([row for row in rows if row["result"] == "PARTIAL_LOSS"]),
        "best_setup_grade_performance": grouped("setup_grade"),
        "best_timeframe_performance": grouped("timeframe"),
        "best_symbol_performance": grouped("symbol"),
        "direction_performance": grouped("direction"),
        "regime_performance": grouped("regime_label"),
        "accepted_vs_rejected": review_grouped(),
        "counter_trend_performance": grouped("trend_alignment"),
        "expectancy_by_regime": grouped("regime_label"),
        "expectancy_by_setup_type": grouped("setup_grade"),
    }
