"""Outcome children of the canonical Telegram outbox, driven by committed tracking.

One row per logical event/recipient. No scan loop, inference or subscription fanout.
An uncertain HTTP send is held, never automatically resent. Published fill facts
are lifecycle locks: competing replay evidence is held for owner review.
"""

from datetime import UTC, datetime, timedelta
import json, math
from app.services.tracking_rules import timestamp, fingerprint

KINDS = {
    "TP1": "TP1",
    "TP2": "TP2",
    "STOP": "STOP_LOSS",
    "RUNNER_STOP": "STOP_LOSS",
    "TIME_EXIT": "TIME_EXIT",
}
LOCKED = ("ATTEMPTED", "SENT", "DELIVERY_UNCERTAIN")


def ensure_schema(c):
    columns = {
        r["name"] for r in c.execute("PRAGMA table_info(telegram_dispatch_config)")
    }
    if "outcome_started_at" not in columns:
        c.execute(
            "ALTER TABLE telegram_dispatch_config ADD COLUMN outcome_started_at TEXT"
        )
    c.executescript("""
    CREATE TABLE IF NOT EXISTS telegram_dispatch_events(
      id INTEGER PRIMARY KEY,dispatch_id INTEGER NOT NULL,event_kind TEXT NOT NULL,
      event_at TEXT NOT NULL,price REAL NOT NULL,quantity REAL NOT NULL,correction_id INTEGER NOT NULL,
      created_at TEXT NOT NULL,UNIQUE(dispatch_id,event_kind),
      FOREIGN KEY(dispatch_id) REFERENCES telegram_dispatches(id),
      FOREIGN KEY(correction_id) REFERENCES tracking_corrections(id));
    CREATE TABLE IF NOT EXISTS telegram_dispatch_event_recipients(
      id INTEGER PRIMARY KEY,event_id INTEGER NOT NULL,original_recipient_id INTEGER NOT NULL,
      status TEXT NOT NULL DEFAULT 'PENDING',attempt_count INTEGER NOT NULL DEFAULT 0,
      last_attempt_at TEXT,next_attempt_at TEXT,sent_at TEXT,telegram_message_id INTEGER,
      failure_code TEXT,reply_to_message_id INTEGER,response_reply_message_id INTEGER,UNIQUE(event_id,original_recipient_id),
      FOREIGN KEY(event_id) REFERENCES telegram_dispatch_events(id),
      FOREIGN KEY(original_recipient_id) REFERENCES telegram_dispatch_recipients(id));
    CREATE INDEX IF NOT EXISTS idx_telegram_event_recipient_due ON telegram_dispatch_event_recipients(status,next_attempt_at);
    CREATE TABLE IF NOT EXISTS telegram_dispatch_event_conflicts(
      id INTEGER PRIMARY KEY,trade_idea_id INTEGER NOT NULL,candidate_hash TEXT NOT NULL,
      observed_at TEXT NOT NULL,reason TEXT NOT NULL,candidate_json TEXT,resolved_at TEXT,
      UNIQUE(trade_idea_id,candidate_hash));
    """)
    receipt_columns = {
        r["name"]
        for r in c.execute("PRAGMA table_info(telegram_dispatch_event_recipients)")
    }
    for column in ("reply_to_message_id", "response_reply_message_id"):
        if column not in receipt_columns:
            c.execute(
                f"ALTER TABLE telegram_dispatch_event_recipients ADD COLUMN {column} INTEGER"
            )
    if "candidate_json" not in {
        r["name"]
        for r in c.execute("PRAGMA table_info(telegram_dispatch_event_conflicts)")
    }:
        c.execute(
            "ALTER TABLE telegram_dispatch_event_conflicts ADD COLUMN candidate_json TEXT"
        )


def accepted_fills(outcome):
    if outcome.get("status") in {"UNRESOLVED", "AMBIGUOUS"} or outcome.get(
        "result"
    ) in {"UNRESOLVED", "AMBIGUOUS"}:
        return {}
    e = outcome.get("tracking_evidence") or {}
    if not e.get("validated_entry_at"):
        return {}
    result = {}
    for leg in e.get("fills", []):
        kind = KINDS.get(leg.get("kind"))
        price = leg.get("price")
        at = leg.get("candle_close_at")
        if (
            kind
            and at
            and isinstance(price, (int, float))
            and math.isfinite(price)
            and price > 0
            and 0 < float(leg.get("quantity", 0)) <= 1
        ):
            result[kind] = {
                "event_at": timestamp(at).isoformat(),
                "price": float(price),
                "quantity": float(leg["quantity"]),
            }
    return result


def hold_conflicting_published_fills(c, trade_id, outcome):
    facts = accepted_fills(outcome)
    locked = c.execute(
        """SELECT e.* FROM telegram_dispatch_events e JOIN telegram_dispatches d ON d.id=e.dispatch_id
      WHERE d.trade_idea_id=? AND EXISTS(SELECT 1 FROM telegram_dispatch_event_recipients r
      WHERE r.event_id=e.id AND r.status IN ('ATTEMPTED','SENT','DELIVERY_UNCERTAIN'))""",
        (trade_id,),
    ).fetchall()
    for old in locked:
        new = facts.get(old["event_kind"])
        if not new or any(new[k] != old[k] for k in ("price", "quantity", "event_at")):
            digest = fingerprint({"status": outcome.get("status"), "facts": facts})
            c.execute(
                "INSERT OR IGNORE INTO telegram_dispatch_event_conflicts(trade_idea_id,candidate_hash,observed_at,reason,candidate_json) VALUES(?,?,?,?,?)",
                (
                    trade_id,
                    digest,
                    datetime.now(UTC).isoformat(),
                    "Published canonical fill differs from replay; projection preserved for review",
                    json.dumps(outcome, default=str, sort_keys=True),
                ),
            )
            return True
    return False


def enqueue(c, trade_id, outcome, correction_id):
    config = c.execute(
        "SELECT outcome_started_at FROM telegram_dispatch_config WHERE id=1"
    ).fetchone()
    if not config or not config[0]:
        return
    trade = c.execute("SELECT * FROM trade_ideas WHERE id=?", (trade_id,)).fetchone()
    if (
        not trade
        or trade["strategy_decision"] != "TRADE"
        or trade["outcome_tracking_mode"] != "PRODUCTION"
    ):
        return
    dispatch = c.execute(
        "SELECT id FROM telegram_dispatches WHERE trade_idea_id=?", (trade_id,)
    ).fetchone()
    if not dispatch:
        return
    if c.execute(
        "SELECT 1 FROM telegram_dispatch_event_conflicts WHERE trade_idea_id=? AND resolved_at IS NULL",
        (trade_id,),
    ).fetchone():
        return
    facts = accepted_fills(outcome)
    now = datetime.now(UTC).isoformat()
    for kind, leg in facts.items():
        if timestamp(leg["event_at"]) < timestamp(config[0]):
            continue
        old = c.execute(
            "SELECT id FROM telegram_dispatch_events WHERE dispatch_id=? AND event_kind=?",
            (dispatch[0], kind),
        ).fetchone()
        if old:
            locked = c.execute(
                "SELECT 1 FROM telegram_dispatch_event_recipients WHERE event_id=? AND status IN ('ATTEMPTED','SENT','DELIVERY_UNCERTAIN')",
                (old[0],),
            ).fetchone()
            if not locked:
                c.execute(
                    "UPDATE telegram_dispatch_events SET event_at=?,price=?,quantity=?,correction_id=? WHERE id=?",
                    (
                        leg["event_at"],
                        leg["price"],
                        leg["quantity"],
                        correction_id,
                        old[0],
                    ),
                )
            event_id = old[0]
        else:
            event_id = c.execute(
                "INSERT INTO telegram_dispatch_events(dispatch_id,event_kind,event_at,price,quantity,correction_id,created_at) VALUES(?,?,?,?,?,?,?)",
                (
                    dispatch[0],
                    kind,
                    leg["event_at"],
                    leg["price"],
                    leg["quantity"],
                    correction_id,
                    now,
                ),
            ).lastrowid
        c.execute(
            """INSERT OR IGNORE INTO telegram_dispatch_event_recipients(event_id,original_recipient_id)
          SELECT ?,r.id FROM telegram_dispatch_recipients r WHERE r.dispatch_id=? AND r.status='SENT'
            AND r.telegram_message_id IS NOT NULL AND datetime(r.sent_at)<=datetime(?)
            AND NOT EXISTS(SELECT 1 FROM telegram_dispatch_recipient_blocks b WHERE b.chat_id=r.chat_id AND b.cleared_at IS NULL)""",
            (event_id, dispatch[0], leg["event_at"]),
        )


def claim(*, now=None, limit=100):
    from app.utils.database import get_connection

    now = now or datetime.now(UTC)
    items = []
    with get_connection() as c:
        c.execute("BEGIN IMMEDIATE")
        # A crashed/unknown send cannot safely be retried with Telegram sendMessage.
        c.execute(
            "UPDATE telegram_dispatch_event_recipients SET status='DELIVERY_UNCERTAIN',failure_code='STALE_SEND_CLAIM' WHERE status='ATTEMPTED' AND datetime(last_attempt_at)<=datetime(?)",
            ((now - timedelta(minutes=5)).isoformat(),),
        )
        candidates = c.execute(
            """SELECT r.id AS recipient_id,e.*,o.chat_id,o.telegram_message_id AS reply_to_message_id,t.symbol,t.direction
          FROM telegram_dispatch_event_recipients r JOIN telegram_dispatch_events e ON e.id=r.event_id
          JOIN telegram_dispatch_recipients o ON o.id=r.original_recipient_id
          JOIN telegram_dispatches d ON d.id=e.dispatch_id JOIN trade_ideas t ON t.id=d.trade_idea_id
          WHERE r.status IN ('PENDING','FAILED_RETRYABLE') AND (r.next_attempt_at IS NULL OR datetime(r.next_attempt_at)<=datetime(?))
            AND o.status='SENT' AND o.telegram_message_id IS NOT NULL
            AND NOT EXISTS(SELECT 1 FROM telegram_dispatch_event_recipients er JOIN telegram_dispatch_events ee ON ee.id=er.event_id
              WHERE er.original_recipient_id=r.original_recipient_id AND ee.dispatch_id=e.dispatch_id
              AND (datetime(ee.event_at)<datetime(e.event_at) OR (ee.event_at=e.event_at AND ee.event_kind='TP1' AND e.event_kind!='TP1'))
              AND er.status IN ('FAILED_RETRYABLE','DELIVERY_UNCERTAIN','ATTEMPTED'))
            AND t.status NOT IN ('AMBIGUOUS','UNRESOLVED') AND t.result NOT IN ('AMBIGUOUS','UNRESOLVED')
            AND NOT EXISTS(SELECT 1 FROM telegram_dispatch_recipient_blocks b WHERE b.chat_id=o.chat_id AND b.cleared_at IS NULL)
            AND NOT EXISTS(SELECT 1 FROM telegram_dispatch_event_conflicts f WHERE f.trade_idea_id=t.id AND f.resolved_at IS NULL)
          ORDER BY e.event_at,CASE e.event_kind WHEN 'TP1' THEN 0 ELSE 1 END,e.id,r.id LIMIT ?""",
            (now.isoformat(), limit),
        ).fetchall()
        for raw in candidates:
            item = dict(raw)
            # Validate against the current committed correction, not a second price engine.
            latest = c.execute(
                "SELECT evidence_json,corrected_json FROM tracking_corrections WHERE asset='CRYPTO' AND signal_id=(SELECT CAST(trade_idea_id AS TEXT) FROM telegram_dispatches WHERE id=?) ORDER BY id DESC LIMIT 1",
                (item["dispatch_id"],),
            ).fetchone()
            if not latest:
                continue
            current = json.loads(latest[1])
            current["tracking_evidence"] = json.loads(latest[0])
            leg = accepted_fills(current).get(item["event_kind"])
            if not leg or any(
                leg[k] != item[k] for k in ("event_at", "price", "quantity")
            ):
                continue
            c.execute(
                "UPDATE telegram_dispatch_event_recipients SET status='ATTEMPTED',attempt_count=attempt_count+1,last_attempt_at=?,reply_to_message_id=COALESCE(reply_to_message_id,?) WHERE id=?",
                (now.isoformat(), item["reply_to_message_id"], item["recipient_id"]),
            )
            items.append(item)
    return items


def recipient_claimed(recipient_id):
    from app.utils.database import get_connection

    with get_connection() as c:
        c.execute("BEGIN IMMEDIATE")
        ready = c.execute(
            """SELECT 1 FROM telegram_dispatch_event_recipients r
          JOIN telegram_dispatch_events e ON e.id=r.event_id
          JOIN telegram_dispatches d ON d.id=e.dispatch_id
          JOIN trade_ideas t ON t.id=d.trade_idea_id
          JOIN telegram_dispatch_recipients o ON o.id=r.original_recipient_id
          WHERE r.id=? AND r.status='ATTEMPTED' AND o.status='SENT'
          AND t.status NOT IN ('UNRESOLVED','AMBIGUOUS') AND t.result NOT IN ('UNRESOLVED','AMBIGUOUS')
          AND NOT EXISTS(SELECT 1 FROM telegram_dispatch_event_conflicts f WHERE f.trade_idea_id=t.id AND f.resolved_at IS NULL)
          AND NOT EXISTS(SELECT 1 FROM telegram_dispatch_recipient_blocks b WHERE b.chat_id=o.chat_id AND b.cleared_at IS NULL)
          AND NOT EXISTS(SELECT 1 FROM telegram_dispatch_event_recipients earlier
            JOIN telegram_dispatch_events ee ON ee.id=earlier.event_id
            WHERE earlier.original_recipient_id=r.original_recipient_id AND ee.dispatch_id=e.dispatch_id
            AND (datetime(ee.event_at)<datetime(e.event_at) OR (ee.event_at=e.event_at AND ee.event_kind='TP1' AND e.event_kind!='TP1'))
            AND earlier.status IN ('PENDING','FAILED_RETRYABLE','ATTEMPTED','DELIVERY_UNCERTAIN'))""",
            (recipient_id,),
        ).fetchone()
        if not ready:
            # No send was attempted by this worker. Release, rather than misclassify as uncertain.
            c.execute(
                "UPDATE telegram_dispatch_event_recipients SET status='PENDING',attempt_count=MAX(0,attempt_count-1),last_attempt_at=NULL WHERE id=? AND status='ATTEMPTED'",
                (recipient_id,),
            )
        return bool(ready)


def finish(
    recipient_id,
    *,
    message_id=None,
    sent_at=None,
    failure=None,
    retry_after=None,
    response_reply_message_id=None,
):
    from app.utils.database import get_connection

    with get_connection() as c:
        if message_id is not None:
            c.execute(
                "UPDATE telegram_dispatch_event_recipients SET status='SENT',telegram_message_id=COALESCE(telegram_message_id,?),sent_at=COALESCE(sent_at,?),response_reply_message_id=COALESCE(response_reply_message_id,?),failure_code=CASE WHEN ? IS NOT NULL AND ?!=reply_to_message_id THEN 'REPLY_MISMATCH' ELSE NULL END WHERE id=? AND attempt_count>0 AND status IN ('ATTEMPTED','DELIVERY_UNCERTAIN','FAILED_PERMANENT')",
                (
                    message_id,
                    (sent_at or datetime.now(UTC)).isoformat(),
                    response_reply_message_id,
                    response_reply_message_id,
                    response_reply_message_id,
                    recipient_id,
                ),
            )
        else:
            status = (
                "FAILED_RETRYABLE"
                if retry_after is not None
                else (
                    "FAILED_PERMANENT"
                    if failure
                    in {
                        "FORBIDDEN",
                        "BLOCKED",
                        "CHAT_NOT_FOUND",
                        "DEACTIVATED",
                        "REPLY_NOT_FOUND",
                        "REJECTED",
                    }
                    else "DELIVERY_UNCERTAIN"
                )
            )
            due = (
                (
                    datetime.now(UTC) + timedelta(seconds=max(1, float(retry_after)))
                ).isoformat()
                if retry_after is not None
                else None
            )
            c.execute(
                "UPDATE telegram_dispatch_event_recipients SET status=?,failure_code=?,next_attempt_at=? WHERE id=? AND status='ATTEMPTED'",
                (status, failure, due, recipient_id),
            )
            if failure in {"FORBIDDEN", "BLOCKED", "CHAT_NOT_FOUND", "DEACTIVATED"}:
                from app.services.telegram_dispatch import _quarantine_recipient

                chat = c.execute(
                    "SELECT o.chat_id FROM telegram_dispatch_event_recipients r JOIN telegram_dispatch_recipients o ON o.id=r.original_recipient_id WHERE r.id=?",
                    (recipient_id,),
                ).fetchone()
                if chat:
                    _quarantine_recipient(
                        c, chat[0], failure, datetime.now(UTC).isoformat()
                    )
