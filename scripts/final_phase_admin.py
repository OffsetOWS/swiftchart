"""Approved-release administration only: additive schema, API/bot switch and rollback."""

import argparse, json, os, subprocess, sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import stage2_admin as previous

PREVIOUS_COMMIT = "f55c8dc29f4240ed21432fede75b98d4af60a23f"


def require_soak(root, commit):
    from scripts.crypto_soak_gate import validate_summary
    manifest = json.loads((root / "release-manifest.json").read_text())
    validate_summary(manifest.get("soak"), commit, manifest["files"])


def prepare(root, previous_root, state, previous_state, commit):
    require_soak(root, commit)
    previous.prepare(
        root,
        previous_root,
        state,
        previous_state,
        commit,
        expected_previous_commit=PREVIOUS_COMMIT,
    )
    config = state / "release-processes.json"
    data = json.loads(config.read_text())
    for app in data["apps"]:
        shell = app["args"][1]
        position = shell.rfind("exec ")
        if position < 0:
            raise ValueError("Saved process lacks exec")
        app["args"] = [
            "-lc",
            shell[:position]
            + "export CRYPTO_SCORE_ARCHIVE_ENABLED=true; "
            + shell[position:],
        ]
    previous.config(config, data["apps"])
    previous.validate_pm2_config(config)


def activate_schema(connection, now=None):
    now = (now or datetime.now(UTC)).isoformat()
    connection.execute(
        "UPDATE telegram_dispatch_config SET outcome_started_at=COALESCE(outcome_started_at,?) WHERE id=1",
        (now,),
    )
    connection.execute(
        "UPDATE score_archive_config SET activated_at=COALESCE(activated_at,?) WHERE id=1",
        (now,),
    )
    return connection.execute(
        "SELECT outcome_started_at FROM telegram_dispatch_config WHERE id=1"
    ).fetchone()[0]


def main():
    p = argparse.ArgumentParser()
    p.add_argument(
        "action",
        choices=["prepare", "backup", "schema", "activate", "verify", "rollback"],
    )
    p.add_argument("--root", type=Path, default=ROOT)
    p.add_argument("--state", type=Path, required=True)
    p.add_argument("--expected-commit")
    p.add_argument(
        "--previous-root",
        type=Path,
        default=Path("/opt/swiftchart-releases") / PREVIOUS_COMMIT,
    )
    p.add_argument(
        "--previous-state",
        type=Path,
        default=Path("/opt/swiftchart-stage2-state") / PREVIOUS_COMMIT,
    )
    p.add_argument("--dependency-root", type=Path, default=Path("/opt/swiftchart"))
    a = p.parse_args()
    if a.action == "prepare":
        if not a.expected_commit:
            raise ValueError("Expected commit required")
        prepare(a.root, a.previous_root, a.state, a.previous_state, a.expected_commit)
        return
    receipt = json.loads((a.state / "state.json").read_text())
    db = Path(receipt["database_path"])
    if a.action != "rollback":
        previous.verify_release(a.root, receipt["release_commit"])
    if a.action == "backup":
        previous.backup_db(db, a.state / "before.db")
    elif a.action == "schema":
        if not (a.state / "before.db").is_file():
            raise ValueError("Backup required before schema")
        env = {
            **os.environ,
            "DATABASE_URL": "sqlite:///" + str(db),
            "PYTHONPATH": str(a.root / "backend") + os.pathsep + str(a.root),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        code = """from app.utils.database import init_db,get_connection
from scripts.final_phase_admin import activate_schema
import json
init_db()
with get_connection() as connection:
    at=activate_schema(connection)
print(json.dumps({'outcomes_started_at':at,'score_capture_enabled_on_restart':True}))
"""
        subprocess.run(
            [str(a.dependency_root / "backend/.venv/bin/python"), "-c", code],
            cwd=a.root,
            env=env,
            check=True,
        )
    elif a.action == "activate":
        previous.switch_processes(a.state / "release-processes.json", a.root)
    elif a.action == "verify":
        previous.verify_process_paths(a.root)
    elif a.action == "rollback":
        old = Path(receipt["previous_source_root"])
        previous.verify_release(old, receipt["previous_commit"])
        if (a.state / "before-rollback.db").exists():
            raise ValueError("Rollback archive already exists")
        previous.stop_release_processes()
        previous.restore_db(a.state / "before.db", db, a.state / "before-rollback.db")
        previous.switch_processes(a.state / "previous-processes.json", old)
    print(json.dumps({"action": a.action, "completed": True}))


if __name__ == "__main__":
    main()
