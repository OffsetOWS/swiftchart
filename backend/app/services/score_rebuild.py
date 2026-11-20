"""Offline score reconstruction from immutable, decision-time evidence."""

import hashlib, json, zlib
from pathlib import Path
import pandas as pd
from app.models.schemas import Zone, LiquiditySweep, MarketRegimeSnapshot
from app.services.score_archive import ROOT, COMPONENTS


def load(connection, evaluation_id):
    row = connection.execute(
        "SELECT payload_json FROM score_evaluations WHERE evaluation_id=?",
        (evaluation_id,),
    ).fetchone()
    if row is None:
        raise LookupError(evaluation_id)
    artifact = json.loads(row[0])
    snapshots = []
    for key in artifact["market_snapshot_refs"]:
        stored = connection.execute(
            "SELECT payload_zlib FROM score_market_snapshots WHERE content_hash=?",
            (key,),
        ).fetchone()
        if stored is None:
            raise ValueError("Missing market snapshot")
        body = zlib.decompress(stored[0])
        if hashlib.sha256(body).hexdigest() != key:
            raise ValueError("Snapshot content mismatch")
        snapshots.append(json.loads(body))
    artifact["market_snapshots"] = snapshots
    return artifact


def rebuild(artifact):
    for name, expected in artifact["source"]["source_hashes"].items():
        if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != expected:
            raise ValueError("Use the pinned scoring source: " + name)
    if all(c["state"] == "NOT_COMPUTED" for c in artifact["components"].values()):
        return {
            "state": "NOT_COMPUTED",
            "reason": artifact["inputs"].get("not_computed_reason"),
            "final": None,
        }
    from app.strategy.trade_ideas import (
        _score_setup,
        _regime_adjustment,
        _signal_quality_control,
    )

    i = artifact["inputs"]
    base, parts = _score_setup(
        **{
            k: i[k]
            for k in (
                "regime",
                "direction",
                "htf_bias",
                "rr",
                "vol_ok",
                "mom_ok",
                "distance_from_mid",
            )
        },
        zone=Zone(**i["zone"]),
        sweep=LiquiditySweep(**i["sweep"]) if i["sweep"] else None
    )
    regime = MarketRegimeSnapshot(**artifact["regime_snapshot"])
    adjusted, delta, note = _regime_adjustment(
        i["direction"], base, regime, artifact["reversal_confirmations"]
    )
    df = pd.DataFrame(artifact["market_snapshots"][0]["rows"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    quality = _signal_quality_control(
        direction=i["direction"],
        df=df,
        score=adjusted,
        regime=i["regime"],
        entry_low=i["entry_low"],
        entry_high=i["entry_high"],
        atr=i["atr"],
        bullish_sweep=(
            LiquiditySweep(**i["bullish_sweep"]) if i.get("bullish_sweep") else None
        ),
        bearish_sweep=(
            LiquiditySweep(**i["bearish_sweep"]) if i.get("bearish_sweep") else None
        ),
    )
    unrounded = float(quality["score"]) if quality["reasons"] else adjusted
    rejected = (
        bool(note and "rejected" in note.lower())
        or quality["status"] == "REJECTED_EXHAUSTED"
        or unrounded < artifact["effective_config"]["internal_pass_floor"]
    )
    checks = {
        "components": all(
            parts[k] == artifact["components"][k]["points"] for k in COMPONENTS
        ),
        "base": base == artifact["stages"][0]["output"],
        "regime": adjusted == artifact["stages"][1]["output"],
        "quality": quality == artifact["stages"][2]["output"],
        "final": round(unrounded, 1) == artifact["stages"][3]["output"],
    }
    if "gate_results" in artifact:
        checks["decision"] = (not rejected) == artifact["gate_results"]["accepted"]
    return {
        "state": "EXACT" if all(checks.values()) else "DRIFT",
        "checks": checks,
        "final": round(unrounded, 1),
        "accepted": not rejected,
    }


def coverage(c):
    summary = dict(c.execute("""SELECT COUNT(*) AS total,
      COALESCE(SUM(initial_score_evaluation_id IS NOT NULL),0) AS archived,
      COALESCE(SUM(initial_score_evaluation_id IS NULL),0) AS legacy_unarchived
      FROM trade_ideas WHERE exchange!='forex'""").fetchone())
    cutoff = c.execute(
        "SELECT activated_at FROM score_archive_config WHERE id=1"
    ).fetchone()[0]
    summary["activated_at"] = cutoff
    summary["new_missing_evidence"] = c.execute(
        "SELECT COUNT(*) FROM trade_ideas WHERE exchange!='forex' AND datetime(created_at)>=datetime(?) AND initial_score_evaluation_id IS NULL",
        (cutoff,),
    ).fetchone()[0]
    summary["new_outbox_missing_evidence"] = c.execute(
        "SELECT COUNT(*) FROM telegram_dispatches d JOIN trade_ideas t ON t.id=d.trade_idea_id WHERE datetime(t.created_at)>=datetime(?) AND d.score_evaluation_id IS NULL",
        (cutoff,),
    ).fetchone()[0]
    return summary
