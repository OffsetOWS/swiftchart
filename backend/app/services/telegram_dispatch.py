from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import json
from typing import Iterable

from app.models.schemas import TradeIdea
from app.utils.database import get_connection


DISPATCH_PENDING = "PENDING"
DISPATCH_ATTEMPTED = "ATTEMPTED"
DISPATCH_SUCCEEDED = "SUCCEEDED"
DISPATCH_PARTIAL = "PARTIAL"
DISPATCH_FAILED_RETRYABLE = "FAILED_RETRYABLE"
DISPATCH_SUPPRESSED_DEDUPE = "SUPPRESSED_DEDUPE"
DISPATCH_PARTIAL_FINAL = "PARTIAL_FINAL"
DISPATCH_FAILED_PERMANENT = "FAILED_PERMANENT"

RECIPIENT_PENDING = "PENDING"
RECIPIENT_ATTEMPTED = "ATTEMPTED"
RECIPIENT_SENT = "SENT"
RECIPIENT_FAILED_RETRYABLE = "FAILED_RETRYABLE"
RECIPIENT_SUPPRESSED_DEDUPE = "SUPPRESSED_DEDUPE"
RECIPIENT_FAILED_PERMANENT = "FAILED_PERMANENT"


@dataclass(frozen=True)
class TelegramDispatchCandidate:
    dispatch_id: int
    trade_idea_id: int
    opportunity_key: str
    idea: TradeIdea
    recipient_chat_ids: tuple[int, ...]
    first_attempt: bool


def _now(value: datetime | None = None) -> datetime:
    current = value or datetime.now(UTC)
    return current if current.tzinfo else current.replace(tzinfo=UTC)


def _iso(value: datetime | None = None) -> str:
    return _now(value).astimezone(UTC).replace(microsecond=0).isoformat()


def _parse_dt(value: str | datetime | None) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return _now(value) if isinstance(value, datetime) else None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _json_list(value: str | list | None) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return []
    return [str(item) for item in parsed] if isinstance(parsed, list) else []


def _idea_from_row(row: dict) -> TradeIdea:
    return TradeIdea(
        symbol=str(row["symbol"]),
        timeframe=str(row["timeframe"]),
        exchange=str(row["exchange"]),
        source=str(row["exchange"]),
        direction="Long" if str(row["direction"]).upper() == "LONG" else "Short",
        market_regime=row.get("market_regime"),
        higher_timeframe_bias=row.get("higher_timeframe_bias") or "HTF_NEUTRAL",
        setup_score=row.get("setup_score"),
        setup_grade=row.get("setup_grade"),
        entry_zone=(float(row["entry_zone_low"]), float(row["entry_zone_high"])),
        stop_loss=float(row["stop_loss"]),
        take_profit_1=float(row["take_profit_1"]),
        take_profit_2=float(row["take_profit_2"]),
        risk_reward_ratio=float(row["risk_reward"]),
        confidence_score=float(row["confidence"]),
        reason=str(row["reason"]),
        invalid_condition=str(row["invalidation"]),
        regime_score=row.get("regime_score"),
        regime_label=row.get("regime_label"),
        trend_alignment=row.get("trend_alignment"),
        regime_confidence_adjustment=float(row.get("regime_confidence_adjustment") or 0),
        reversal_confirmations=_json_list(row.get("reversal_confirmations")),
        regime_explanation=row.get("regime_explanation"),
        entry_status=row.get("entry_status") or "READY",
        signal_candle_time=_parse_dt(row.get("signal_candle_time")),
        setup_family=row.get("setup_family"),
        strategy_version=row.get("strategy_version"),
        edge_status=row.get("edge_status"),
        strategy_decision=row.get("strategy_decision"),
        v2_decision_reason=row.get("v2_decision_reason"),
        regime_confidence=row.get("regime_confidence"),
        entry_quality_status=row.get("entry_quality_status"),
        entry_quality_score=row.get("entry_quality_score"),
        entry_quality_reason=row.get("entry_quality_reason"),
        outcome_tracking_mode=row.get("outcome_tracking_mode"),
        v2_evaluated_at=_parse_dt(row.get("v2_evaluated_at")),
        opportunity_key=row.get("opportunity_key"),
        retest_confirmed_at=_parse_dt(row.get("retest_confirmed_at")),
        executable_at=_parse_dt(row.get("executable_at")),
        production_rule_accepted=(bool(row["production_rule_accepted"]) if row.get("production_rule_accepted") is not None else None),
        strict_trend_short_eligible=(bool(row["strict_trend_short_eligible"]) if row.get("strict_trend_short_eligible") is not None else None),
        strict_trigger_type=row.get("strict_trigger_type"),
        strict_confirmation_type=row.get("strict_confirmation_type"),
        strict_trigger_candle_time=_parse_dt(row.get("strict_trigger_candle_time")),
        strict_trigger_candle_completed=(bool(row["strict_trigger_candle_completed"]) if row.get("strict_trigger_candle_completed") is not None else None),
    )


def canonical_dispatch_cutoff() -> datetime:
    with get_connection() as connection:
        row = connection.execute(
            "SELECT canonical_started_at FROM telegram_dispatch_config WHERE id = 1"
        ).fetchone()
    if row is None:
        raise RuntimeError("Telegram canonical dispatch cutoff is not initialized")
    return _parse_dt(row["canonical_started_at"]) or datetime.now(UTC)


def claim_telegram_dispatches(
    subscriber_chat_ids: Iterable[int],
    *,
    limit: int = 50,
    now: datetime | None = None,
    attempted_retry_after_seconds: int = 300,
) -> list[TelegramDispatchCandidate]:
    current = _now(now)
    now_iso = _iso(current)
    stale_attempt = _iso(current - timedelta(seconds=max(0, attempted_retry_after_seconds)))
    subscribers = tuple(sorted({int(chat_id) for chat_id in subscriber_chat_ids}))
    candidates: list[TelegramDispatchCandidate] = []

    with get_connection() as connection:
        # The bot loop and webhook-triggered poll can overlap. Acquire a write
        # reservation before reading so only one poll can lease a recipient.
        connection.execute("BEGIN IMMEDIATE")
        cutoff = connection.execute(
            "SELECT canonical_started_at FROM telegram_dispatch_config WHERE id = 1"
        ).fetchone()
        if cutoff is None:
            raise RuntimeError("Telegram canonical dispatch cutoff is not initialized")
        blocked = {row["chat_id"] for row in connection.execute(
            "SELECT chat_id FROM telegram_dispatch_recipient_blocks WHERE cleared_at IS NULL")}
        subscribers = tuple(chat_id for chat_id in subscribers if str(chat_id) not in blocked)
        rows = connection.execute(
            """
            SELECT t.*
            FROM trade_ideas t
            LEFT JOIN telegram_dispatches d ON d.trade_idea_id = t.id
            WHERE t.strategy_version IS NOT NULL
              AND t.strategy_decision = 'TRADE'
              AND t.entry_status = 'READY'
              AND t.executable_at IS NOT NULL
              AND t.lifecycle_status = 'active'
              AND t.opportunity_key IS NOT NULL
              AND t.result = 'OPEN'
              AND t.status IN ('PENDING', 'OPEN')
              AND datetime(COALESCE(t.executable_at, t.created_at)) >= datetime(?)
              AND (t.expires_at IS NULL OR datetime(t.expires_at) >= datetime(?))
              AND (d.id IS NULL OR d.status NOT IN ('SUCCEEDED', 'SUPPRESSED_DEDUPE', 'PARTIAL_FINAL', 'FAILED_PERMANENT'))
              AND ((? AND (d.id IS NULL OR d.recipients_initialized_at IS NULL)) OR EXISTS (
                  SELECT 1 FROM telegram_dispatch_recipients r WHERE r.dispatch_id=d.id
                    AND (r.status='PENDING'
                         OR (r.status='FAILED_RETRYABLE' AND (r.next_attempt_at IS NULL OR datetime(r.next_attempt_at)<=datetime(?)))
                         OR (r.status='ATTEMPTED' AND datetime(r.last_attempt_at)<=datetime(?)))))
            ORDER BY datetime(COALESCE(t.executable_at, t.created_at)) ASC, t.id ASC
            LIMIT ?
            """,
            (cutoff["canonical_started_at"], now_iso, bool(subscribers), now_iso, stale_attempt, min(250, max(1, int(limit)))),
        ).fetchall()

        for raw_row in rows:
            row = dict(raw_row)
            connection.execute(
                """
                INSERT OR IGNORE INTO telegram_dispatches (
                    trade_idea_id, opportunity_key, status, eligible_at, created_at, updated_at
                ) VALUES (?, ?, 'PENDING', ?, ?, ?)
                """,
                (
                    row["id"],
                    row["opportunity_key"],
                    row.get("executable_at") or row["created_at"],
                    now_iso,
                    now_iso,
                ),
            )
            dispatch = dict(
                connection.execute(
                    "SELECT * FROM telegram_dispatches WHERE trade_idea_id = ?",
                    (row["id"],),
                ).fetchone()
            )

            if dispatch.get("recipients_initialized_at") is None and subscribers:
                for chat_id in subscribers:
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO telegram_dispatch_recipients (
                            dispatch_id, chat_id, status, created_at, updated_at
                        ) VALUES (?, ?, 'PENDING', ?, ?)
                        """,
                        (dispatch["id"], str(chat_id), now_iso, now_iso),
                    )
                connection.execute(
                    """
                    UPDATE telegram_dispatches
                    SET recipients_initialized_at = ?, updated_at = ?
                    WHERE id = ? AND recipients_initialized_at IS NULL
                    """,
                    (now_iso, now_iso, dispatch["id"]),
                )

            recipients = connection.execute(
                """
                SELECT id, chat_id
                FROM telegram_dispatch_recipients
                WHERE dispatch_id = ?
                  AND (
                    (status='PENDING' OR (status='FAILED_RETRYABLE' AND (next_attempt_at IS NULL OR datetime(next_attempt_at)<=datetime(?))))
                    OR (status = 'ATTEMPTED' AND datetime(last_attempt_at) <= datetime(?))
                  )
                ORDER BY id
                """,
                (dispatch["id"], now_iso, stale_attempt),
            ).fetchall()
            claimed_chat_ids: list[int] = []
            for recipient in recipients:
                cursor = connection.execute(
                    """
                    UPDATE telegram_dispatch_recipients
                    SET status = 'ATTEMPTED', attempt_count = attempt_count + 1,
                        last_attempt_at = ?, updated_at = ?
                    WHERE id = ?
                      AND (
                        (status='PENDING' OR (status='FAILED_RETRYABLE' AND (next_attempt_at IS NULL OR datetime(next_attempt_at)<=datetime(?))))
                        OR (status = 'ATTEMPTED' AND datetime(last_attempt_at) <= datetime(?))
                      )
                    """,
                    (now_iso, now_iso, recipient["id"], now_iso, stale_attempt),
                )
                if cursor.rowcount:
                    claimed_chat_ids.append(int(recipient["chat_id"]))
            chat_ids = tuple(claimed_chat_ids)
            if not chat_ids:
                continue
            connection.execute(
                """
                UPDATE telegram_dispatches
                SET status = 'ATTEMPTED', attempt_count = attempt_count + 1,
                    first_attempt_at = COALESCE(first_attempt_at, ?),
                    last_attempt_at = ?, updated_at = ?
                WHERE id = ? AND status NOT IN ('SUCCEEDED', 'SUPPRESSED_DEDUPE')
                """,
                (now_iso, now_iso, now_iso, dispatch["id"]),
            )
            candidates.append(
                TelegramDispatchCandidate(
                    dispatch_id=int(dispatch["id"]),
                    trade_idea_id=int(row["id"]),
                    opportunity_key=str(row["opportunity_key"]),
                    idea=_idea_from_row(row),
                    recipient_chat_ids=chat_ids,
                    first_attempt=int(dispatch.get("attempt_count") or 0) == 0,
                )
            )
    return candidates


def _refresh_dispatch(connection, dispatch_id: int, timestamp: str) -> str:
    counts = {
        row["status"]: int(row["count"])
        for row in connection.execute(
            """
            SELECT status, COUNT(*) AS count
            FROM telegram_dispatch_recipients
            WHERE dispatch_id = ?
            GROUP BY status
            """,
            (dispatch_id,),
        ).fetchall()
    }
    total = sum(counts.values())
    sent = counts.get(RECIPIENT_SENT, 0)
    final = sent + counts.get(RECIPIENT_FAILED_PERMANENT, 0) + counts.get(RECIPIENT_SUPPRESSED_DEDUPE, 0)
    if total and sent == total:
        status = DISPATCH_SUCCEEDED
    elif total and final == total:
        status = DISPATCH_PARTIAL_FINAL if sent else DISPATCH_FAILED_PERMANENT
    elif sent:
        status = DISPATCH_PARTIAL
    elif counts.get(RECIPIENT_FAILED_RETRYABLE, 0):
        status = DISPATCH_FAILED_RETRYABLE
    elif counts.get(RECIPIENT_ATTEMPTED, 0):
        status = DISPATCH_ATTEMPTED
    else:
        status = DISPATCH_PENDING
    connection.execute(
        """
        UPDATE telegram_dispatches
        SET status = ?, dispatched_at = CASE WHEN ? IN ('SUCCEEDED','PARTIAL_FINAL') THEN COALESCE(dispatched_at, ?) ELSE dispatched_at END,
            updated_at = ?
        WHERE id = ?
        """,
        (status, status, timestamp, timestamp, dispatch_id),
    )
    return status


def mark_telegram_recipient_success(
    dispatch_id: int,
    chat_id: int,
    *,
    telegram_message_id: int | None = None,
    now: datetime | None = None,
) -> str:
    timestamp = _iso(now)
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE telegram_dispatch_recipients
            SET status = 'SENT', sent_at = COALESCE(sent_at, ?),
                telegram_message_id = COALESCE(telegram_message_id, ?),
                last_error = NULL, next_attempt_at = NULL, updated_at = ?
            WHERE dispatch_id = ? AND chat_id = ?
            """,
            (timestamp, telegram_message_id, timestamp, dispatch_id, str(int(chat_id))),
        )
        return _refresh_dispatch(connection, dispatch_id, timestamp)


def permanent_telegram_failure_reason(error: str) -> str | None:
    text = str(error).lower()
    if "bot was blocked" in text or "blocked by the user" in text:
        return "BLOCKED"
    if "chat not found" in text:
        return "CHAT_NOT_FOUND"
    if "user is deactivated" in text:
        return "DEACTIVATED"
    if text.startswith("forbidden:") or "bot was kicked" in text:
        return "FORBIDDEN"
    return None


def _quarantine_recipient(connection, chat_id: str, reason: str, timestamp: str) -> int:
    dispatch_ids = [row[0] for row in connection.execute("""SELECT DISTINCT dispatch_id
        FROM telegram_dispatch_recipients WHERE chat_id=? AND status NOT IN ('SENT','SUPPRESSED_DEDUPE','FAILED_PERMANENT')""", (chat_id,))]
    connection.execute("""INSERT INTO telegram_dispatch_recipient_blocks(chat_id,reason_code,blocked_at)
        VALUES (?,?,?) ON CONFLICT(chat_id) DO UPDATE SET
        reason_code=excluded.reason_code,blocked_at=excluded.blocked_at,cleared_at=NULL""", (chat_id,reason,timestamp))
    cursor = connection.execute("""UPDATE telegram_dispatch_recipients
        SET status='FAILED_PERMANENT', next_attempt_at=NULL,
            last_error=COALESCE(last_error,?),updated_at=?
        WHERE chat_id=? AND status NOT IN ('SENT','SUPPRESSED_DEDUPE','FAILED_PERMANENT')""", (reason,timestamp,chat_id))
    for dispatch_id in dispatch_ids:
        _refresh_dispatch(connection,dispatch_id,timestamp)
    return cursor.rowcount


def mark_telegram_recipient_failure(
    dispatch_id: int, chat_id: int, error: str, *, now: datetime | None = None,
    permanent_reason: str | None = None, retry_after_seconds: float = 0,
) -> str:
    current = _now(now)
    timestamp = _iso(current)
    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        recipient = connection.execute("SELECT status FROM telegram_dispatch_recipients WHERE dispatch_id=? AND chat_id=?",
                                       (dispatch_id,str(int(chat_id)))).fetchone()
        if recipient is None or recipient["status"] == RECIPIENT_SENT:
            return _refresh_dispatch(connection,dispatch_id,timestamp)
        reason = permanent_reason or permanent_telegram_failure_reason(error)
        if reason:
            _quarantine_recipient(connection,str(int(chat_id)),reason,timestamp)
        else:
            next_attempt = _iso(current+timedelta(seconds=max(0,retry_after_seconds)))
            connection.execute("""UPDATE telegram_dispatch_recipients
                SET status='FAILED_RETRYABLE', last_error=?, next_attempt_at=?, updated_at=?
                WHERE dispatch_id=? AND chat_id=? AND status != 'SENT'""",
                (str(error)[:500],next_attempt,timestamp,dispatch_id,str(int(chat_id))))
        return _refresh_dispatch(connection,dispatch_id,timestamp)


def recipient_is_claimed(dispatch_id: int, chat_id: int) -> bool:
    with get_connection() as connection:
        return connection.execute("""SELECT 1 FROM telegram_dispatch_recipients r
            WHERE r.dispatch_id=? AND r.chat_id=? AND r.status='ATTEMPTED'
            AND NOT EXISTS (SELECT 1 FROM telegram_dispatch_recipient_blocks b
                            WHERE b.chat_id=r.chat_id AND b.cleared_at IS NULL)""", (dispatch_id,str(int(chat_id)))).fetchone() is not None


def clear_crypto_recipient_block(chat_id: int, *, now: datetime | None = None) -> None:
    """Explicit re-subscription affects future dispatches; preserve old receipts."""
    with get_connection() as connection:
        connection.execute("UPDATE telegram_dispatch_recipient_blocks SET cleared_at=? WHERE chat_id=?", (_iso(now),str(int(chat_id))))


def migrate_permanent_telegram_failures(*, now: datetime | None = None) -> dict:
    """Run after the approved backup: close known permanent crypto failures only."""
    timestamp = _iso(now)
    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        rows = connection.execute("""SELECT chat_id,last_error FROM telegram_dispatch_recipients
            WHERE status NOT IN ('SENT','SUPPRESSED_DEDUPE','FAILED_PERMANENT') AND last_error IS NOT NULL""").fetchall()
        blocks = {row["chat_id"]:reason for row in rows
                  if (reason := permanent_telegram_failure_reason(row["last_error"]))}
        affected = sum(_quarantine_recipient(connection,chat_id,reason,timestamp) for chat_id,reason in blocks.items())
    return {"blocked_crypto_recipients":len(blocks),"permanent_failure_rows":affected}


def suppress_telegram_dispatch_for_dedupe(
    dispatch_id: int,
    reason: str,
    *,
    now: datetime | None = None,
) -> None:
    timestamp = _iso(now)
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE telegram_dispatch_recipients
            SET status = 'SUPPRESSED_DEDUPE', last_error = ?, updated_at = ?
            WHERE dispatch_id = ? AND status != 'SENT'
            """,
            (str(reason)[:500], timestamp, dispatch_id),
        )
        connection.execute(
            """
            UPDATE telegram_dispatches
            SET status = 'SUPPRESSED_DEDUPE', last_error = ?, updated_at = ?
            WHERE id = ?
            """,
            (str(reason)[:500], timestamp, dispatch_id),
        )


def telegram_dispatch_record(trade_idea_id: int) -> dict | None:
    with get_connection() as connection:
        row = connection.execute(
            "SELECT * FROM telegram_dispatches WHERE trade_idea_id = ?",
            (trade_idea_id,),
        ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["recipients"] = [
            dict(recipient)
            for recipient in connection.execute(
                "SELECT * FROM telegram_dispatch_recipients WHERE dispatch_id = ? ORDER BY id",
                (row["id"],),
            ).fetchall()
        ]
    return result
