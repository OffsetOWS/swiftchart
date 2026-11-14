import asyncio, json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
import pandas as pd
import pytest
from telegram.error import Forbidden, RetryAfter, TimedOut
from app.services import outcome_events as events, telegram_dispatch as dispatch
from app.services.trade_history import (
    save_trade_ideas,
    evaluate_trade,
    update_outcome,
    list_active_crypto_trades,
)
from app.utils import database
from bot.alerts import dispatch_crypto_outcomes
from bot.formatter import format_trade_alert, format_outcome_event
from test_canonical_telegram_dispatch import configure_db, idea

START = datetime(2026, 1, 1, tzinfo=UTC)


def setup(monkeypatch, tmp_path, scenario="TIME_EXIT", subscribers="101,202,303"):
    configure_db(monkeypatch, tmp_path, subscribers)
    candidate = idea()
    identifier = save_trade_ideas([candidate])[0]
    claimed = dispatch.claim_telegram_dispatches([101, 202, 303])[0]
    for chat in (101, 202):
        dispatch.mark_telegram_recipient_success(
            claimed.dispatch_id,
            chat,
            telegram_message_id=chat + 1000,
            now=START + timedelta(minutes=2),
        )
    with database.get_connection() as c:
        c.execute(
            "UPDATE trade_ideas SET created_at=?,executable_at=?,expires_at=? WHERE id=?",
            (
                (START + timedelta(minutes=1)).isoformat(),
                (START + timedelta(minutes=1)).isoformat(),
                (START + timedelta(hours=24)).isoformat(),
                identifier,
            ),
        )
        c.execute(
            "UPDATE telegram_dispatch_config SET outcome_started_at=? WHERE id=1",
            (START.isoformat(),),
        )
        row = dict(
            c.execute("SELECT * FROM trade_ideas WHERE id=?", (identifier,)).fetchone()
        )
    candles = []
    for n in range(12):
        o, h, l, cl = (101, 104, 99, 102) if n == 0 else (105, 108, 103, 106)
        if scenario in {"TP1", "TP2", "RUNNER_STOP"} and n == 1:
            o, h, l, cl = 105, 112, 103, 111
        if scenario == "TP2" and n == 2:
            o, h, l, cl = 111, 119, 104, 118
        if scenario == "STOP_LOSS" and n == 1:
            o, h, l, cl = 102, 104, 94, 96
        if scenario == "RUNNER_STOP" and n == 2:
            o, h, l, cl = 106, 108, 94, 95
        if scenario == "AMBIGUOUS" and n == 1:
            o, h, l, cl = 105, 120, 94, 100
        candles.append(
            dict(
                timestamp=START + timedelta(hours=4 * (n + 1)),
                open=o,
                high=h,
                low=l,
                close=cl,
                volume=1000,
                complete=True,
            )
        )
    if scenario == "TP1":
        candles = candles[:2]
    outcome = evaluate_trade(
        row,
        pd.DataFrame(candles),
        first_delivery=START + timedelta(minutes=2),
        as_of=START + timedelta(hours=4 * (len(candles) + 1)),
    )
    return identifier, row, candles, outcome


@pytest.mark.parametrize(
    "scenario,kinds",
    [
        ("TP1", ["TP1"]),
        ("TP2", ["TP1", "TP2"]),
        ("STOP_LOSS", ["STOP_LOSS"]),
        ("RUNNER_STOP", ["TP1", "STOP_LOSS"]),
        ("TIME_EXIT", ["TIME_EXIT"]),
        ("AMBIGUOUS", []),
    ],
)
def test_factual_events_reply_only_to_original_deliveries_once(
    monkeypatch, tmp_path, scenario, kinds
):
    identifier, row, candles, outcome = setup(monkeypatch, tmp_path, scenario)
    update_outcome(identifier, outcome)
    update_outcome(identifier, outcome)
    sent = []

    async def send_message(**kw):
        sent.append(kw)
        return SimpleNamespace(
            message_id=800 + len(sent),
            date=datetime.now(UTC),
            reply_to_message=SimpleNamespace(message_id=kw["reply_to_message_id"]),
        )

    bot = SimpleNamespace(send_message=send_message)
    result = asyncio.run(dispatch_crypto_outcomes(bot))
    assert result["sent"] == 2 * len(kinds)
    assert asyncio.run(dispatch_crypto_outcomes(bot))["sent"] == 0
    assert {s["chat_id"] for s in sent} <= {101, 202}
    for message in sent:
        assert message["reply_to_message_id"] == message["chat_id"] + 1000
        assert message["allow_sending_without_reply"] is False
        assert all(
            word not in message["text"].lower()
            for word in ("score", "grade", "win rate", "profit", "streak", "total")
        )
    with database.get_connection() as c:
        actual = [
            r[0]
            for r in c.execute(
                "SELECT event_kind FROM telegram_dispatch_events ORDER BY id"
            )
        ]
        assert actual == kinds
        for receipt in c.execute("SELECT * FROM telegram_dispatch_event_recipients"):
            assert receipt["status"] == "SENT" and receipt["attempt_count"] == 1
            assert (
                receipt["reply_to_message_id"] == receipt["response_reply_message_id"]
            )


def test_cutoff_disabled_and_shadow_never_fan_out(monkeypatch, tmp_path):
    identifier, row, candles, outcome = setup(monkeypatch, tmp_path)
    with database.get_connection() as c:
        c.execute(
            "UPDATE telegram_dispatch_config SET outcome_started_at=NULL WHERE id=1"
        )
    update_outcome(identifier, outcome)
    assert events.claim() == []
    with database.get_connection() as c:
        c.execute(
            "UPDATE telegram_dispatch_config SET outcome_started_at=? WHERE id=1",
            ((START + timedelta(days=10)).isoformat(),),
        )
    update_outcome(identifier, outcome)
    assert events.claim() == []
    with database.get_connection() as c:
        c.execute(
            "UPDATE telegram_dispatch_config SET outcome_started_at=? WHERE id=1",
            (START.isoformat(),),
        )
        c.execute(
            "UPDATE trade_ideas SET outcome_tracking_mode='SHADOW' WHERE id=?",
            (identifier,),
        )
    update_outcome(identifier, outcome)
    assert events.claim() == []


@pytest.mark.parametrize(
    "error,status",
    [
        (Forbidden("blocked"), "FAILED_PERMANENT"),
        (TimedOut("unknown"), "DELIVERY_UNCERTAIN"),
        (RetryAfter(60), "FAILED_RETRYABLE"),
    ],
)
def test_failures_are_safe_and_later_events_wait_for_earlier_receipts(
    monkeypatch, tmp_path, error, status
):
    identifier, row, candles, outcome = setup(monkeypatch, tmp_path, "TP2")
    update_outcome(identifier, outcome)
    calls = []

    async def send_message(**kw):
        calls.append(kw)
        raise error

    asyncio.run(dispatch_crypto_outcomes(SimpleNamespace(send_message=send_message)))
    # One attempt per original recipient. TP2 must wait behind its TP1.
    assert len(calls) == 2
    with database.get_connection() as c:
        assert {
            r[0]
            for r in c.execute(
                "SELECT r.status FROM telegram_dispatch_event_recipients r JOIN telegram_dispatch_events e ON e.id=r.event_id WHERE e.event_kind='TP1'"
            )
        } == {status}
        assert all(
            r[0] in {"PENDING", "FAILED_PERMANENT"}
            for r in c.execute(
                "SELECT r.status FROM telegram_dispatch_event_recipients r JOIN telegram_dispatch_events e ON e.id=r.event_id WHERE e.event_kind='TP2'"
            )
        )
    assert (
        events.claim(now=datetime.now(UTC) + timedelta(seconds=1)) == []
        or status == "DELIVERY_UNCERTAIN"
    )


def test_replay_cannot_overwrite_a_published_fill_and_preserves_competing_evidence(
    monkeypatch, tmp_path
):
    identifier, row, candles, outcome = setup(monkeypatch, tmp_path, "TP1")
    update_outcome(identifier, outcome)
    claimed = events.claim()
    assert len(claimed) == 2
    for item in claimed:
        events.finish(item["recipient_id"], message_id=77)
    candidate = deepcopy(outcome)
    candidate["outcome_checked_at"] = (
        datetime.now(UTC) + timedelta(minutes=1)
    ).isoformat()
    candidate["tracking_evidence"]["fills"][0]["price"] += 1
    update_outcome(identifier, candidate)
    with database.get_connection() as c:
        latest = json.loads(
            c.execute(
                "SELECT evidence_json FROM tracking_corrections ORDER BY id DESC LIMIT 1"
            ).fetchone()[0]
        )
        assert (
            latest["fills"][0]["price"]
            == outcome["tracking_evidence"]["fills"][0]["price"]
        )
        rejected = json.loads(
            c.execute(
                "SELECT candidate_json FROM telegram_dispatch_event_conflicts"
            ).fetchone()[0]
        )
        assert (
            rejected["tracking_evidence"]["fills"][0]["price"]
            == candidate["tracking_evidence"]["fills"][0]["price"]
        )
    assert events.claim() == []


def test_claim_lease_and_uncertain_send_never_auto_resend(monkeypatch, tmp_path):
    identifier, row, candles, outcome = setup(monkeypatch, tmp_path)
    update_outcome(identifier, outcome)
    now = datetime.now(UTC)
    first = events.claim(now=now)
    assert len(first) == 2
    assert events.claim(now=now) == []
    assert events.claim(now=now + timedelta(minutes=6)) == []
    with database.get_connection() as c:
        assert {
            r[0]
            for r in c.execute("SELECT status FROM telegram_dispatch_event_recipients")
        } == {"DELIVERY_UNCERTAIN"}


def test_48h_clock_and_active_signal_survive_old_24h_expiration(monkeypatch, tmp_path):
    identifier, row, candles, outcome = setup(monkeypatch, tmp_path, "TP1")
    active = list_active_crypto_trades()
    assert len(active) == 1
    assert active[0]["tracking_deadline"] == START + timedelta(hours=52)
    assert len(format_trade_alert(idea())) < 4096


def test_unfinalized_last_candle_cannot_become_time_exit(monkeypatch, tmp_path):
    identifier, row, candles, outcome = setup(monkeypatch, tmp_path)
    assert outcome["status"] == "TIME_EXIT"
    candles[-1]["complete"] = False
    guarded = evaluate_trade(
        row,
        pd.DataFrame(candles),
        first_delivery=START + timedelta(minutes=2),
        as_of=START + timedelta(days=3),
    )
    assert guarded["status"] != "TIME_EXIT"


def test_read_only_live_audit_reports_observed_and_pending_event_types(
    monkeypatch, tmp_path
):
    from scripts.final_phase_audit import audit

    identifier, row, candles, outcome = setup(monkeypatch, tmp_path, "TP1")
    update_outcome(identifier, outcome)
    for item in events.claim():
        events.finish(
            item["recipient_id"],
            message_id=44,
            response_reply_message_id=item["reply_to_message_id"],
        )
    report = audit(tmp_path / "swiftchart.db", START.isoformat())
    assert report["outcomes"]["sent_by_event"] == {"TP1": 2}
    assert report["outcomes"]["tracker_mismatches"] == 0
    assert report["outcomes"]["original_reply_mismatches"] == 0
    assert report["outcomes"]["response_reply_unverified"] == 0
    assert report["pending_live_event_types"] == ["TP2", "STOP_LOSS", "TIME_EXIT"]


def test_delivered_tp1_runner_remains_visible_until_tracker_settles_it(
    monkeypatch, tmp_path
):
    identifier, row, candles, outcome = setup(monkeypatch, tmp_path, "TP1")
    update_outcome(identifier, outcome)
    active = list_active_crypto_trades()
    assert len(active) == 1 and active[0]["status"] == "TP1_HIT"
    assert active[0]["result"] == "OPEN" and active[0]["lifecycle_status"] == "tp1_hit"


def test_published_fill_keeps_existing_protected_history_save_semantics(
    monkeypatch, tmp_path
):
    identifier, row, candles, outcome = setup(monkeypatch, tmp_path, "TP1")
    update_outcome(identifier, outcome)
    repeated = idea(entry_zone=(100.5, 102.5))
    assert save_trade_ideas([repeated]) == []
    with database.get_connection() as c:
        stored = c.execute(
            "SELECT status,entry_zone_low FROM trade_ideas WHERE id=?", (identifier,)
        ).fetchone()
        assert tuple(stored) == ("TP1_HIT", 100.0)
        assert c.execute("SELECT COUNT(*) FROM trade_ideas").fetchone()[0] == 1


def test_factual_canonical_delivery_does_not_wait_for_unused_market_context(
    monkeypatch, tmp_path
):
    from bot import alerts
    from unittest.mock import AsyncMock

    configure_db(monkeypatch, tmp_path)
    save_trade_ideas([idea()])
    context = AsyncMock(
        side_effect=AssertionError("Delivery must not fetch market context")
    )
    monkeypatch.setattr(alerts, "_btc_context", context)

    async def send_message(**kw):
        return SimpleNamespace(message_id=71, date=datetime.now(UTC))

    result = asyncio.run(
        alerts.dispatch_crypto_outbox(SimpleNamespace(send_message=send_message))
    )
    assert result["sent"] == 1
    context.assert_not_awaited()


def test_late_confirmed_send_is_recorded_even_if_another_worker_quarantines_chat(
    monkeypatch, tmp_path
):
    identifier, row, candles, outcome = setup(monkeypatch, tmp_path, "TP1")
    update_outcome(identifier, outcome)
    item = next(x for x in events.claim() if int(x["chat_id"]) == 101)
    with database.get_connection() as c:
        dispatch._quarantine_recipient(
            c, "101", "FORBIDDEN", datetime.now(UTC).isoformat()
        )
    events.finish(
        item["recipient_id"],
        message_id=909,
        response_reply_message_id=item["reply_to_message_id"],
    )
    with database.get_connection() as c:
        receipt = c.execute(
            "SELECT status,telegram_message_id FROM telegram_dispatch_event_recipients WHERE id=?",
            (item["recipient_id"],),
        ).fetchone()
        assert tuple(receipt) == ("SENT", 909)
        assert (
            c.execute(
                "SELECT cleared_at FROM telegram_dispatch_recipient_blocks WHERE chat_id='101'"
            ).fetchone()[0]
            is None
        )
    assert not any(
        int(x["chat_id"]) == 101
        for x in events.claim(now=datetime.now(UTC) + timedelta(minutes=10))
    )
