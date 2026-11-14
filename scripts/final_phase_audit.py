"""Read-only release observation. Prints aggregates, never chat IDs or message bodies."""

import argparse, json, sqlite3, statistics, sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.services.tracking_rules import timestamp
from app.services.outcome_events import accepted_fills
from app.services.score_rebuild import coverage


def audit(database, since=None):
    c = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    try:
        c.execute("BEGIN")
        if since is None:
            since = c.execute(
                "SELECT outcome_started_at FROM telegram_dispatch_config WHERE id=1"
            ).fetchone()[0]
        if not since:
            raise ValueError("Release cutoff is not activated")
        rows = c.execute(
            """SELECT t.created_at,r.sent_at,r.status,d.trade_idea_id FROM telegram_dispatches d
          JOIN trade_ideas t ON t.id=d.trade_idea_id JOIN telegram_dispatch_recipients r ON r.dispatch_id=d.id
          WHERE datetime(t.created_at)>=datetime(?)""",
            (since,),
        ).fetchall()
        delays = []
        first = {}
        states = {}
        for row in rows:
            states[row["status"]] = states.get(row["status"], 0) + 1
            if row["status"] != "SENT" or not row["sent_at"]:
                continue
            seconds = (
                timestamp(row["sent_at"]) - timestamp(row["created_at"])
            ).total_seconds()
            delays.append(seconds)
            key = row["trade_idea_id"]
            first[key] = min(first.get(key, float("inf")), seconds)

        def metric(values):
            return {
                "count": len(values),
                "median_seconds": statistics.median(values) if values else None,
                "worst_seconds": max(values) if values else None,
            }

        report = {
            "observed_at": datetime.now(UTC).isoformat(),
            "since": since,
            "initial_recipient_statuses": states,
            "creation_to_first_delivery": metric(list(first.values())),
            "creation_to_all_recipient_deliveries": metric(delays),
            "score_archive": coverage(c),
        }
        sent = c.execute(
            """SELECT e.*,r.reply_to_message_id,r.response_reply_message_id,o.telegram_message_id AS original_message_id,
          o.status AS original_status,d.trade_idea_id FROM telegram_dispatch_event_recipients r
          JOIN telegram_dispatch_events e ON e.id=r.event_id JOIN telegram_dispatch_recipients o ON o.id=r.original_recipient_id
          JOIN telegram_dispatches d ON d.id=e.dispatch_id WHERE r.status='SENT' AND datetime(e.event_at)>=datetime(?)""",
            (since,),
        ).fetchall()
        kinds = {}
        mismatched = 0
        unverified = 0
        bad_original = 0
        for row in sent:
            kinds[row["event_kind"]] = kinds.get(row["event_kind"], 0) + 1
            latest = c.execute(
                "SELECT corrected_json,evidence_json FROM tracking_corrections WHERE asset='CRYPTO' AND signal_id=? ORDER BY id DESC LIMIT 1",
                (str(row["trade_idea_id"]),),
            ).fetchone()
            current = json.loads(latest[0])
            current["tracking_evidence"] = json.loads(latest[1])
            leg = accepted_fills(current).get(row["event_kind"])
            if not leg or any(
                leg[k] != row[k] for k in ("price", "quantity", "event_at")
            ):
                mismatched += 1
            if (
                row["reply_to_message_id"] != row["original_message_id"]
                or row["original_status"] != "SENT"
            ):
                bad_original += 1
            if row["response_reply_message_id"] is None:
                unverified += 1
            elif row["response_reply_message_id"] != row["original_message_id"]:
                bad_original += 1
        report["outcomes"] = {
            "sent_by_event": kinds,
            "tracker_mismatches": mismatched,
            "original_reply_mismatches": bad_original,
            "response_reply_unverified": unverified,
            "recipient_statuses": {
                r[0]: r[1]
                for r in c.execute(
                    "SELECT status,COUNT(*) FROM telegram_dispatch_event_recipients GROUP BY status"
                )
            },
            "open_replay_conflicts": c.execute(
                "SELECT COUNT(*) FROM telegram_dispatch_event_conflicts WHERE resolved_at IS NULL"
            ).fetchone()[0],
            "duplicate_event_recipient_rows": c.execute(
                "SELECT COUNT(*) FROM (SELECT event_id,original_recipient_id FROM telegram_dispatch_event_recipients GROUP BY 1,2 HAVING COUNT(*)>1)"
            ).fetchone()[0],
        }
        report["pending_live_event_types"] = [
            k for k in ("TP1", "TP2", "STOP_LOSS", "TIME_EXIT") if not kinds.get(k)
        ]
        return report
    finally:
        c.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--database", type=Path, required=True)
    p.add_argument("--since")
    a = p.parse_args()
    result = audit(a.database, a.since)
    print(json.dumps(result, indent=2))
    raise SystemExit(
        1
        if result["outcomes"]["tracker_mismatches"]
        or result["outcomes"]["original_reply_mismatches"]
        or result["outcomes"]["duplicate_event_recipient_rows"]
        else 0
    )
