from copy import deepcopy
from datetime import UTC, datetime, timedelta
import json, shlex
from pathlib import Path
import pytest
from scripts import final_phase_admin as admin, final_phase_frontend as front
from app.utils import database
from test_canonical_telegram_dispatch import configure_db


def test_final_prepare_retains_previous_source_and_enables_only_reviewed_capture(
    monkeypatch, tmp_path
):
    old = tmp_path / "previous"
    new = tmp_path / "new"
    oldstate = tmp_path / "oldstate"
    state = tmp_path / "state"
    for p in (old, new, oldstate):
        p.mkdir()
    db = tmp_path / "live.db"
    db.touch()
    (oldstate / "state.json").write_text(
        json.dumps(
            {
                "release_commit": admin.PREVIOUS_COMMIT,
                "release_root": str(old),
                "database_path": str(db),
            }
        )
    )
    shell = (
        "set -a; source /opt/swiftchart/.env; set +a; export DATABASE_URL="
        + shlex.quote("sqlite:///" + str(db))
        + "; export BOT_STATE_PATH=/opt/swiftchart/.swiftchart_bot_state.json; exec /opt/swiftchart/backend/.venv/bin/python -m app"
    )
    apps = [
        {
            "name": name,
            "cwd": str(old),
            "script": "/usr/bin/bash",
            "args": ["-lc", shell],
            "interpreter": "none",
        }
        for name in sorted(admin.previous.NAMES)
    ]
    monkeypatch.setattr(admin, "require_soak", lambda *_: None)
    monkeypatch.setattr(admin.previous, "verify_release", lambda *_: None)
    monkeypatch.setattr(admin.previous, "verify_process_paths", lambda *_: None)
    monkeypatch.setattr(admin.previous, "processes", lambda _: deepcopy(apps))
    monkeypatch.setattr(admin.previous, "validate_pm2_config", lambda _: None)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "DO_NOT_SERIALIZE_RUNTIME_AUTH")
    admin.prepare(new, old, state, oldstate, "candidate")
    assert json.loads((state / "previous-processes.json").read_text())["apps"] == apps
    after = json.loads((state / "release-processes.json").read_text())
    for app in after["apps"]:
        assert app["cwd"] == str(new)
        assert "export CRYPTO_SCORE_ARCHIVE_ENABLED=true; exec " in app["args"][1]
        assert "FEE" not in app["args"][1] and "FUNDING" not in app["args"][1]
    assert all(
        "DO_NOT_SERIALIZE_RUNTIME_AUTH" not in p.read_text() for p in state.iterdir()
    )
    assert (
        json.loads((state / "state.json").read_text())["previous_commit"]
        == admin.PREVIOUS_COMMIT
    )


def test_cutoff_activation_is_idempotent_and_preserves_legacy_records(
    monkeypatch, tmp_path
):
    configure_db(monkeypatch, tmp_path)
    first = datetime(2026, 11, 14, tzinfo=UTC)
    with database.get_connection() as c:
        assert (
            c.execute(
                "SELECT outcome_started_at FROM telegram_dispatch_config"
            ).fetchone()[0]
            is None
        )
        assert admin.activate_schema(c, first) == first.isoformat()
        assert admin.activate_schema(c, first + timedelta(days=1)) == first.isoformat()
        assert (
            c.execute("SELECT activated_at FROM score_archive_config").fetchone()[0]
            == first.isoformat()
        )
        assert (
            c.execute("SELECT COUNT(*) FROM telegram_dispatch_events").fetchone()[0]
            == 0
        )


def test_frontend_health_failure_prevents_publication(monkeypatch, tmp_path):
    state = tmp_path / "state.json"
    receipt = {
        "release_commit": "candidate",
        "previous": {"deployment_id": "old", "url": "old.example"},
    }
    state.write_text(json.dumps(receipt))
    monkeypatch.setattr(front, "current_alias", lambda _: receipt["previous"])

    def unhealthy(_):
        raise ValueError("backend unhealthy")

    monkeypatch.setattr(front, "backend_ready", unhealthy)
    monkeypatch.setattr(
        front,
        "cli",
        lambda *_args, **_kw: pytest.fail("No deployment before backend health"),
    )
    with pytest.raises(ValueError, match="unhealthy"):
        front.publish(Path("cli"), state)


def test_frontend_alias_drift_prevents_replacing_another_release(monkeypatch, tmp_path):
    state = tmp_path / "state.json"
    state.write_text(
        json.dumps(
            {
                "release_commit": "candidate",
                "previous": {"deployment_id": "old", "url": "old.example"},
            }
        )
    )
    monkeypatch.setattr(
        front,
        "current_alias",
        lambda _: {"deployment_id": "newer", "url": "newer.example"},
    )
    monkeypatch.setattr(
        front, "backend_ready", lambda _: pytest.fail("Stop before deployment")
    )
    with pytest.raises(ValueError, match="changed after preparation"):
        front.publish(Path("cli"), state)


def test_frontend_source_upload_excludes_backend_and_private_files(
    monkeypatch, tmp_path
):
    names = {
        "a": "frontend/src/App.jsx",
        "b": "vercel.ts",
        "c": ".vercelignore",
        "d": "backend/app/main.py",
        "e": "analysis_outputs/private.json",
        "f": ".env",
    }

    def output(args, **_kw):
        if args[3] == "ls-tree":
            return b"".join(
                ("100644 blob " + oid + "\t" + name + "\0").encode()
                for oid, name in names.items()
            )
        return b"source"

    monkeypatch.setattr(front.subprocess, "check_output", output)
    front.source_workspace("candidate", tmp_path)
    assert {
        str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*") if p.is_file()
    } == {"frontend/src/App.jsx", "vercel.ts", ".vercelignore"}


@pytest.mark.parametrize(
    "state_data", [{"published": True}, {"staged_deployment": {"id": "already-built"}}]
)
def test_frontend_never_creates_a_second_deploy_silently(tmp_path, state_data):
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"release_commit": "candidate", **state_data}))
    with pytest.raises(ValueError):
        front.publish(Path("cli"), state)
