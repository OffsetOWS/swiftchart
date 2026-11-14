"""Immutable decision-time score evidence. Never serialize the application environment."""

from contextvars import ContextVar
from contextlib import contextmanager
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
import hashlib, json, math, subprocess, uuid, zlib

EXTERNAL_CONTEXT = ContextVar("crypto_score_external_context", default=None)
CONTEXT = ContextVar("crypto_score_context", default=None)
ROOT = Path(__file__).resolve().parents[3]
COMPONENTS = ("regime", "zone", "sweep", "htf", "rr", "momentum", "distance")


def normal(v):
    if v is None or isinstance(v, (str, bool, int)):
        return v
    if isinstance(v, float):
        return v if math.isfinite(v) else {"nonfinite": str(v)}
    if isinstance(v, datetime):
        return v.isoformat()
    if hasattr(v, "item"):
        return normal(v.item())
    if hasattr(v, "model_dump"):
        return normal(v.model_dump(mode="json"))
    if isinstance(v, dict):
        return {str(k): normal(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [normal(x) for x in v]
    raise TypeError("Unsupported score evidence value: " + type(v).__name__)


def encode(v):
    return json.dumps(normal(v), sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(v):
    return hashlib.sha256(encode(v).encode()).hexdigest()


@lru_cache(maxsize=1)
def source_version():
    manifest = ROOT / "release-manifest.json"
    if manifest.exists():
        commit = json.loads(manifest.read_text())["release_commit"]
    else:
        try:
            commit = subprocess.check_output(
                ["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True
            ).strip()
        except (OSError, subprocess.CalledProcessError):
            commit = "UNKNOWN"
    files = [
        ROOT / "backend/app/strategy" / name
        for name in (
            "trade_ideas.py",
            "market_structure.py",
            "strict_trend_short.py",
            "market_regime.py",
            "support_resistance.py",
            "liquidity_sweep.py",
            "decision_engine.py",
            "edge_registry.py",
        )
    ]
    import importlib.metadata, sys

    installed = {
        name: importlib.metadata.version(name)
        for name in ("pandas", "numpy", "pydantic")
    }
    return {
        "python": sys.version.split()[0],
        "installed_versions": installed,
        "source_commit": commit,
        "source_hashes": {
            str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in files
        },
        "dependency_hash": (
            hashlib.sha256((ROOT / "backend/requirements.txt").read_bytes()).hexdigest()
            if (ROOT / "backend/requirements.txt").exists()
            else json.loads(manifest.read_text()).get("dependency_hash", "UNKNOWN")
        ),
    }


def candle_snapshot(df, *, exchange, symbol, timeframe):
    from app.services.tracking_rules import timestamp, timeframe_duration

    rows = []
    for raw in df.to_dict("records"):
        row = dict(raw)
        opened = timestamp(row["timestamp"])
        row.update(
            opened_at=opened.isoformat(),
            closed_at=(
                (opened + timeframe_duration(timeframe)).isoformat()
                if timeframe
                else None
            ),
            available_at=df.attrs.get("fetched_at"),
            finalized=row.get("complete"),
        )
        rows.append(row)
    return normal(
        {
            "exchange": exchange,
            "symbol": symbol,
            "timeframe": timeframe,
            "source": df.attrs.get("source_snapshot"),
            "requested_at": df.attrs.get("requested_at"),
            "fetched_at": df.attrs.get("fetched_at"),
            "rows": rows,
            "market_metadata": {
                k: v
                for k, v in (df.attrs.get("market_metadata") or {}).items()
                if k
                in {
                    "exchange",
                    "symbol",
                    "exchange_symbol",
                    "active",
                    "perpVolume24h",
                    "perp_volume_24h",
                    "volume_24h",
                    "dayNtlVlm",
                    "volume",
                    "base_asset",
                    "quote_asset",
                    "market_data_timestamp",
                }
            },
            "knowledge_state": (
                "CAPTURED" if df.attrs.get("requested_at") else "LEGACY_UNTIMED_INPUT"
            ),
        }
    )


@contextmanager
def capture_context(
    symbol, timeframe, exchange, df, risk, htf_dfs, global_score, breadth
):
    from app.config import get_settings

    if not get_settings().crypto_score_archive_enabled:
        yield
        return
    frames = [
        candle_snapshot(df, exchange=exchange, symbol=symbol, timeframe=timeframe)
    ]
    for htf in htf_dfs or []:
        frames.append(
            candle_snapshot(
                htf,
                exchange=exchange,
                symbol=symbol,
                timeframe=htf.attrs.get("timeframe")
                or (
                    None
                    if len(htf) < 2
                    else str(
                        int(
                            (
                                htf["timestamp"].iloc[1] - htf["timestamp"].iloc[0]
                            ).total_seconds()
                            / 60
                        )
                    )
                    + "m"
                ),
            )
        )
    # Large scan evidence belongs to task context, never pandas attrs: pandas
    # deep-copies attrs on slices/columns, multiplying the entire scan per op.
    external = EXTERNAL_CONTEXT.get() or []
    frames.extend(external)
    token = CONTEXT.set(
        {
            "evaluation_run_id": uuid.uuid4().hex,
            "evaluated_at": datetime.now(UTC).isoformat(),
            "market_snapshots": frames,
            "global_regime_score": global_score,
            "breadth_above_ma_pct": breadth,
            "risk_settings": normal(risk),
        }
    )
    try:
        yield
    finally:
        CONTEXT.reset(token)


def make_evidence(
    *,
    symbol,
    timeframe,
    exchange,
    direction,
    df,
    risk,
    inputs,
    parts=None,
    base=None,
    regime_adjusted=None,
    quality=None,
    final=None,
    regime=None,
    confirmations=None,
):
    from app.config import get_settings

    if not get_settings().crypto_score_archive_enabled:
        return None
    context = CONTEXT.get() or {
        "evaluation_run_id": uuid.uuid4().hex,
        "evaluated_at": datetime.now(UTC).isoformat(),
        "market_snapshots": [
            candle_snapshot(df, exchange=exchange, symbol=symbol, timeframe=timeframe)
        ],
        "risk_settings": normal(risk),
        "context_state": "DIRECT_CALL_NO_EXTERNAL_CONTEXT",
    }
    from app.config import get_settings

    if not get_settings().crypto_score_archive_enabled:
        return None
    snapshots = context["market_snapshots"]
    value = {
        "schema_version": "score-evidence-v1",
        "evaluation_id": uuid.uuid4().hex,
        "evaluation_run_id": context["evaluation_run_id"],
        "evaluated_at": context["evaluated_at"],
        "market": {
            "symbol": symbol,
            "timeframe": timeframe,
            "exchange": exchange,
            "direction": direction,
        },
        "source": source_version(),
        "effective_config": {
            "internal_pass_floor": 65,
            "allow_no_trade_range_softening": __import__(
                "app.config", fromlist=["get_settings"]
            )
            .get_settings()
            .allow_no_trade_range_softening,
            "risk": context["risk_settings"],
        },
        "inputs": normal(inputs),
        "components": {
            k: {
                "state": "COMPUTED" if parts is not None else "NOT_COMPUTED",
                "points": parts[k] if parts is not None else None,
            }
            for k in COMPONENTS
        },
        "component_metadata": {
            k: v for k, v in (parts or {}).items() if k not in COMPONENTS
        },
        "stages": [
            {
                "stage": "component_sum_then_cap_100",
                "raw_sum": (
                    sum(parts[k] for k in COMPONENTS) if parts is not None else None
                ),
                "cap": 100,
                "output": base,
            },
            {"stage": "regime_adjustment", "output": regime_adjusted},
            {"stage": "quality_penalty_and_cap", "output": normal(quality)},
            {"stage": "round_1_decimal", "output": final},
        ],
        "regime_snapshot": normal(regime),
        "reversal_confirmations": confirmations or [],
        "external_context": {
            k: context.get(k) for k in ("global_regime_score", "breadth_above_ma_pct")
        },
        "market_snapshot_refs": [digest(s) for s in snapshots],
        "market_snapshots": snapshots,
    }
    branches = {
        "zone": (
            "strength_score"
            if inputs.get("zone", {}).get("strength_score")
            else "strength_times_100"
        ),
        "sweep": (
            "quality_score"
            if (inputs.get("sweep") or {}).get("sweep_quality_score")
            else (
                "strength_times_100"
                if inputs.get("sweep")
                else "no_sweep_regime_fallback"
            )
        ),
    }
    component_inputs = {
        "regime": ("regime", "direction"),
        "zone": ("zone",),
        "sweep": ("sweep", "regime"),
        "htf": ("htf_bias", "direction"),
        "rr": (
            "entry_low",
            "entry_high",
            "stop",
            "tp1",
            "tp2",
            "rr",
            "rr_unrounded",
            "rr_rounding",
        ),
        "momentum": ("vol_ok", "mom_ok", "atr"),
        "distance": ("distance_from_mid", "zone", "entry_low", "entry_high"),
    }
    for name, component in value["components"].items():
        component.update(
            version="score-evidence-v1",
            branch=branches.get(name, "mapping-v1"),
            raw_inputs={k: inputs.get(k) for k in component_inputs[name]},
        )
    if parts is not None:
        value["components"]["momentum"]["raw_inputs"].update(
            volume_period=20,
            volume_multiplier=1.25,
            momentum_period=14,
            momentum_return_threshold=0.025,
            momentum_atr_multiplier=2.2,
            last_volume=float(df["volume"].iloc[-1]),
            mean_volume_20=float(df["volume"].tail(20).mean()),
            return_14=(
                float(df["close"].pct_change(14).iloc[-1]) if len(df) > 14 else None
            ),
            close_minus_14=(
                float(df["close"].iloc[-1] - df["close"].iloc[-14])
                if len(df) >= 14
                else None
            ),
        )
    value["rounding"] = (
        "binary64 round-trip JSON; Python int truncation and round(score,1)"
    )
    value["stages"][1].update(
        input=base,
        adjustment=None if base is None else regime_adjusted - base,
        cap=100,
        output=regime_adjusted,
    )
    if quality is not None:
        value["stages"][2].update(
            input=regime_adjusted, **quality.get("archive_details", {})
        )
    return normal(value)


def ensure_schema(c):
    c.executescript("""
    CREATE TABLE IF NOT EXISTS score_archive_config(id INTEGER PRIMARY KEY CHECK(id=1),activated_at TEXT);
    INSERT OR IGNORE INTO score_archive_config(id) VALUES(1);
    CREATE TABLE IF NOT EXISTS score_market_snapshots(content_hash TEXT PRIMARY KEY,payload_zlib BLOB NOT NULL,created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS score_evaluations(evaluation_id TEXT PRIMARY KEY,content_hash TEXT NOT NULL,payload_json TEXT NOT NULL,created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS score_component_values(evaluation_id TEXT NOT NULL,component_name TEXT NOT NULL,state TEXT NOT NULL,points REAL,PRIMARY KEY(evaluation_id,component_name));
    CREATE TABLE IF NOT EXISTS score_adjustment_steps(evaluation_id TEXT NOT NULL,stage_index INTEGER NOT NULL,step_json TEXT NOT NULL,PRIMARY KEY(evaluation_id,stage_index));
    CREATE TABLE IF NOT EXISTS score_opportunity_evaluations(id INTEGER PRIMARY KEY,trade_idea_id INTEGER NOT NULL,evaluation_id TEXT NOT NULL,decision_json TEXT NOT NULL,created_at TEXT NOT NULL,UNIQUE(trade_idea_id,evaluation_id));
    """)
    for table in (
        "score_market_snapshots",
        "score_evaluations",
        "score_component_values",
        "score_adjustment_steps",
        "score_opportunity_evaluations",
    ):
        for action in ("UPDATE", "DELETE"):
            c.execute(
                f"CREATE TRIGGER IF NOT EXISTS {table}_no_{action.lower()} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT,'Score archive is append-only'); END"
            )
    component_columns = {
        r["name"] for r in c.execute("PRAGMA table_info(score_component_values)")
    }
    for column in ("component_version", "branch", "raw_inputs_json"):
        if column not in component_columns:
            c.execute(f"ALTER TABLE score_component_values ADD COLUMN {column} TEXT")
    for table, column in (
        ("trade_ideas", "initial_score_evaluation_id"),
        ("trade_ideas", "score_evaluation_id"),
        ("telegram_dispatches", "score_evaluation_id"),
        ("signal_reviews", "score_evaluation_id"),
    ):
        cols = {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}
        if column not in cols:
            c.execute(f"ALTER TABLE {table} ADD COLUMN {column} TEXT")
    if "market_volume_24h" not in {
        r["name"] for r in c.execute("PRAGMA table_info(trade_ideas)")
    }:
        c.execute("ALTER TABLE trade_ideas ADD COLUMN market_volume_24h REAL")


def persist(c, evidence):
    if not evidence:
        return None
    payload = dict(evidence)
    snapshots = payload.pop("market_snapshots", [])
    now = datetime.now(UTC).isoformat()
    for snapshot in snapshots:
        body = encode(snapshot).encode()
        key = hashlib.sha256(body).hexdigest()
        c.execute(
            "INSERT OR IGNORE INTO score_market_snapshots VALUES(?,?,?)",
            (key, zlib.compress(body), now),
        )
    body = encode(payload)
    key = hashlib.sha256(body.encode()).hexdigest()
    identifier = payload["evaluation_id"]
    previous = c.execute(
        "SELECT content_hash FROM score_evaluations WHERE evaluation_id=?",
        (identifier,),
    ).fetchone()
    if previous and previous[0] != key:
        raise ValueError("Evaluation ID reused with different evidence")
    c.execute(
        "INSERT OR IGNORE INTO score_evaluations VALUES(?,?,?,?)",
        (identifier, key, body, now),
    )
    for name, part in payload["components"].items():
        c.execute(
            "INSERT OR IGNORE INTO score_component_values(evaluation_id,component_name,state,points,component_version,branch,raw_inputs_json) VALUES(?,?,?,?,?,?,?)",
            (
                identifier,
                name,
                part["state"],
                part["points"],
                part["version"],
                part["branch"],
                encode(part["raw_inputs"]),
            ),
        )
    for index, step in enumerate(payload["stages"]):
        c.execute(
            "INSERT OR IGNORE INTO score_adjustment_steps VALUES(?,?,?)",
            (identifier, index, encode(step)),
        )
    return identifier


def link(c, idea, trade_id, *, make_current=True):
    from app.services.crypto_exit_policy import deadline
    from app.services.tracking_rules import timestamp

    row = c.execute(
        "SELECT created_at,opportunity_key,score_evaluation_id FROM trade_ideas WHERE id=?",
        (trade_id,),
    ).fetchone()
    idea.history_created_at = timestamp(row[0])
    idea.tracking_deadline = deadline(row[0], idea.timeframe)
    identifier = persist(c, idea.score_evaluation)
    if make_current:
        c.execute(
            "UPDATE trade_ideas SET market_volume_24h=COALESCE(?,market_volume_24h) WHERE id=?",
            (idea.market_volume_24h, trade_id),
        )
    if not identifier:
        return
    decision = {
        "opportunity_key": row["opportunity_key"],
        "parent_evaluation_id": row["score_evaluation_id"],
        "observation_only": not make_current,
        "gate_results": getattr(idea, "score_evaluation", {}).get("gate_results"),
        "candidate_opportunity_key": idea.opportunity_key,
        "strategy_version": idea.strategy_version,
        "strategy_decision": idea.strategy_decision,
        "entry_status": idea.entry_status,
        "outcome_tracking_mode": idea.outcome_tracking_mode,
        "market_volume_24h": idea.market_volume_24h,
        "score": idea.setup_score,
    }
    c.execute(
        "INSERT OR IGNORE INTO score_opportunity_evaluations(trade_idea_id,evaluation_id,decision_json,created_at) VALUES(?,?,?,?)",
        (trade_id, identifier, encode(decision), datetime.now(UTC).isoformat()),
    )
    if make_current:
        c.execute(
            "UPDATE trade_ideas SET initial_score_evaluation_id=COALESCE(initial_score_evaluation_id,?),score_evaluation_id=? WHERE id=?",
            (identifier, identifier, trade_id),
        )
        if (
            idea.strategy_decision == "TRADE"
            and idea.entry_status == "READY"
            and idea.outcome_tracking_mode == "PRODUCTION"
        ):
            now = datetime.now(UTC).isoformat()
            c.execute(
                """INSERT OR IGNORE INTO telegram_dispatches(trade_idea_id,opportunity_key,status,eligible_at,created_at,updated_at,score_evaluation_id)
              SELECT id,opportunity_key,'PENDING',COALESCE(executable_at,created_at),?,?,? FROM trade_ideas WHERE id=?""",
                (now, now, identifier, trade_id),
            )
            c.execute(
                "UPDATE telegram_dispatches SET score_evaluation_id=? WHERE trade_idea_id=? AND attempt_count=0",
                (identifier, trade_id),
            )
