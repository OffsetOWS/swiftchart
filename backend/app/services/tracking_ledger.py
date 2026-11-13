"""Append-only correction history, written atomically with current projections."""
import json
from app.services.tracking_rules import RULE_VERSION, fingerprint


def ensure_tracking_ledger(connection):
    connection.execute("""CREATE TABLE IF NOT EXISTS tracking_corrections (
        id INTEGER PRIMARY KEY AUTOINCREMENT, asset TEXT NOT NULL,
        signal_id TEXT NOT NULL, observed_at TEXT NOT NULL, rule_version TEXT NOT NULL,
        previous_id INTEGER, original_json TEXT NOT NULL, corrected_json TEXT NOT NULL,
        evidence_json TEXT NOT NULL, content_hash TEXT NOT NULL,
        FOREIGN KEY(previous_id) REFERENCES tracking_corrections(id))""")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_tracking_corrections_signal ON tracking_corrections(asset,signal_id,id)")
    for action in ("UPDATE", "DELETE"):
        connection.execute(f"""CREATE TRIGGER IF NOT EXISTS tracking_corrections_no_{action.lower()}
            BEFORE {action} ON tracking_corrections BEGIN
            SELECT RAISE(ABORT, 'Tracking correction ledger is append-only'); END""")


def record_correction(connection, asset, signal_id, before, after, evidence, observed_at):
    ensure_tracking_ledger(connection)
    # Repeated observation times are not a new correction, but new evidence is.
    stable = {k:v for k,v in after.items() if k not in {"outcome_checked_at", "last_price_updated_at", "latest_price_at"}}
    digest = fingerprint(dict(after=stable, evidence=evidence))
    prior = connection.execute("SELECT id, content_hash FROM tracking_corrections WHERE asset=? AND signal_id=? ORDER BY id DESC LIMIT 1", (asset, str(signal_id))).fetchone()
    if prior and prior["content_hash"] == digest:
        return prior["id"]
    encode = lambda v:json.dumps(v,sort_keys=True,default=str,allow_nan=False)
    cursor = connection.execute("""INSERT INTO tracking_corrections
        (asset,signal_id,observed_at,rule_version,previous_id,original_json,corrected_json,evidence_json,content_hash)
        VALUES (?,?,?,?,?,?,?,?,?)""", (asset,str(signal_id),str(observed_at),RULE_VERSION,
            prior["id"] if prior else None,encode(before),encode(after),encode(evidence),digest))
    return cursor.lastrowid


def guard_crypto_transition(before, after):
    after = dict(after)
    evidence = dict(after.get("tracking_evidence") or {})
    evidence["flags"] = list(evidence.get("flags", []))
    old = json.loads(before.get("lifecycle_events") or "[]")
    confirmed = [e.get("at") for e in old if e.get("event") == "ENTRY_CONFIRMED" and e.get("at")]
    historical_entry = before.get("entry_triggered_at") or next(iter(confirmed), None)
    if historical_entry:
        if after.get("result") == "NO_ENTRY" or not evidence.get("validated_entry_at"):
            after["entry_triggered_at"] = historical_entry
            after.update(status="UNRESOLVED",result="UNRESOLVED",pnl_r_multiple=None,closed_at=None)
            evidence["category"] = "unresolved"
            evidence["flags"].append("PRESERVED_HISTORICAL_ENTRY_REQUIRES_EVIDENCE")
        elif confirmed and any(at != after.get("entry_triggered_at") for at in confirmed):
            after.update(entry_triggered_at=historical_entry, status="UNRESOLVED",
                         result="UNRESOLVED", pnl_r_multiple=None, closed_at=None)
            evidence["category"] = "unresolved"
            evidence["flags"].append("CONFIRMED_ENTRY_CHANGED_IN_REPLAY")
    if before.get("closed_at") and after.get("result") == "OPEN":
        after.update(status="UNRESOLVED",result="UNRESOLVED",pnl_r_multiple=None,closed_at=None)
        evidence["category"] = "unresolved"
        evidence["flags"].append("TERMINAL_CANNOT_REOPEN_WITHOUT_CORRECTION")
    if set(evidence["flags"]) & {"CONFIRMED_ENTRY_CHANGED_IN_REPLAY", "PRESERVED_HISTORICAL_ENTRY_REQUIRES_EVIDENCE", "TERMINAL_CANNOT_REOPEN_WITHOUT_CORRECTION"} and evidence.get("validated_entry_at"):
        # Retain the competing replay for audit, not as accepted fill evidence.
        evidence["rejected_replay"] = {k:evidence.get(k) for k in
            ("validated_entry_at", "fills", "booked_r", "remaining_quantity", "events")}
        evidence.update(validated_entry_at=None, fills=[], booked_r=None, remaining_quantity=None, events=[])
        after["lifecycle_events"] = "[]"
    new = json.loads(after.get("lifecycle_events") or "[]")
    events = {fingerprint(e):e for e in old+new}
    after["lifecycle_events"] = json.dumps(list(events.values()), default=str)
    after["tracking_evidence"] = evidence
    return after


def first_crypto_delivery(connection, trade_id):
    tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not {"telegram_dispatches", "telegram_dispatch_recipients"} <= tables:
        return None
    rows = connection.execute("""SELECT r.sent_at
        FROM telegram_dispatches d JOIN telegram_dispatch_recipients r ON r.dispatch_id=d.id
        WHERE d.trade_idea_id=? AND r.status='SENT' AND r.sent_at IS NOT NULL""",(trade_id,)).fetchall()
    from app.services.tracking_rules import timestamp
    receipts = [r["sent_at"] for r in rows]
    return min(receipts, key=timestamp) if receipts else None
