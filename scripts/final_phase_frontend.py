"""Prepare locally, then publish the pinned frontend only after explicit approval."""

import argparse, json, os, re, shlex, subprocess, tempfile
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
PROJECT = "prj_ISXZ98mUW2ymsbiBvIKfLIWNDeog"
SCOPE = "offsetows-projects"
BASE = "f55c8dc29f4240ed21432fede75b98d4af60a23f"
DOMAIN = "swiftchart.xyz"


def cli(binary, *args, cwd=None, env=None):
    command = (
        ["node", str(binary)] if str(binary).endswith(".js") else [str(binary)]
    ) + list(args)
    effective_env = (
        env
        if env is not None
        else {**os.environ, "SWIFTCHART_BACKEND_ORIGIN": "http://localhost"}
    )
    result = subprocess.run(
        command, cwd=cwd or ROOT, env=effective_env, capture_output=True, text=True
    )
    if result.returncode:
        raise RuntimeError(
            "Vercel command failed: "
            + args[0]
            + " (output withheld; may contain deployment configuration)"
        )
    return result.stdout


def api(binary, path):
    return json.loads(cli(binary, "api", path, "--scope", SCOPE))


def current_alias(binary):
    data = api(binary, "/v4/aliases?projectId=" + PROJECT + "&limit=100")
    matched = [a for a in data["aliases"] if a["alias"] == DOMAIN]
    if len(matched) != 1 or matched[0].get("projectId") != PROJECT:
        raise ValueError("Canonical frontend alias not found in the expected project")
    row = matched[0]
    return {"deployment_id": row["deploymentId"], "url": row["deployment"]["url"]}


def prepare(binary, state, commit, link):
    actual = subprocess.check_output(
        ["git", "-C", str(ROOT), "rev-parse", commit + "^{commit}"], text=True
    ).strip()
    if not re.fullmatch(r"[a-f0-9]{40}", actual):
        raise ValueError("Immutable commit required")
    project = json.loads(
        cli(binary, "project", "inspect", "swiftchart", "--json", "--scope", SCOPE)
    )
    if project["id"] != PROJECT or project["rootDirectory"] is not None:
        raise ValueError("Frontend project configuration drift")
    metadata = json.loads(link.read_text())
    if metadata["projectId"] != PROJECT:
        raise ValueError("Wrong local Vercel project link")
    before = json.loads(
        subprocess.check_output(
            ["git", "-C", str(ROOT), "show", BASE + ":vercel.json"], text=True
        )
    )
    origin = urlparse(before["rewrites"][0]["destination"])
    # Retain the existing backend route privately, never in a new Git commit.
    backend_origin = origin.scheme + "://" + origin.netloc
    receipt = {
        "release_commit": actual,
        "project_id": PROJECT,
        "org_id": metadata["orgId"],
        "previous": current_alias(binary),
        "backend_origin": backend_origin,
        "prepared_at": datetime.now(UTC).isoformat(),
    }
    state.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(state, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(receipt, stream, indent=2)
    return {
        "prepared": True,
        "release_commit": actual,
        "previous_deployment_id": receipt["previous"]["deployment_id"],
    }


def backend_ready(commit):
    code = """import json,pathlib,subprocess,sys,urllib.request
sha=sys.argv[1];root=pathlib.Path('/opt/swiftchart-releases')/sha
manifest=json.loads((root/'release-manifest.json').read_text())
assert manifest['release_commit']==sha
rows=[x for x in json.loads(subprocess.check_output(['pm2','jlist'])) if x['name'] in {'swiftchart-api','swiftchart-bot'}]
assert len(rows)==2 and {x['name'] for x in rows}=={'swiftchart-api','swiftchart-bot'}
assert all(x['pm2_env']['status']=='online' and pathlib.Path(x['pm2_env']['pm_cwd']).resolve()==root.resolve() and (pathlib.Path('/proc')/str(x['pid'])/'cwd').resolve()==root.resolve() for x in rows)
with urllib.request.urlopen('http://localhost:8000/health',timeout=20) as r:assert r.status==200
print(json.dumps({'release_commit':sha,'healthy':True}))
"""
    command = "python3 -c " + shlex.quote(code) + " " + shlex.quote(commit)
    data = json.loads(
        subprocess.check_output(["ssh", "swiftchart-vps", command], text=True)
    )
    if data != {"release_commit": commit, "healthy": True}:
        raise ValueError("Backend health receipt mismatch")


def source_workspace(commit, target):
    entries = subprocess.check_output(
        ["git", "-C", str(ROOT), "ls-tree", "-r", "-z", commit]
    )
    for raw in entries.split(b"\0"):
        if not raw:
            continue
        info, name = raw.split(b"\t", 1)
        mode, kind, oid = info.decode().split()
        name = name.decode()
        if not (name.startswith("frontend/") or name in {"vercel.ts", ".vercelignore"}):
            continue
        if kind != "blob" or mode not in {"100644", "100755"}:
            raise ValueError("Unsupported frontend source path")
        path = target / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(
            subprocess.check_output(["git", "-C", str(ROOT), "cat-file", "blob", oid])
        )


def publish(binary, state):
    receipt = json.loads(state.read_text())
    sha = receipt["release_commit"]
    if receipt.get("staged_deployment"):
        raise ValueError(
            "An existing staged release must be reconciled before creating another deployment"
        )
    if receipt.get("published"):
        raise ValueError("This release was already published; refusing a second deploy")
    if current_alias(binary) != receipt["previous"]:
        raise ValueError("Frontend production changed after preparation; stop")
    backend_ready(sha)
    with tempfile.TemporaryDirectory(prefix="swiftchart-frontend-") as folder:
        target = Path(folder)
        source_workspace(sha, target)
        (target / ".vercel").mkdir()
        (target / ".vercel/project.json").write_text(
            json.dumps({"projectId": PROJECT, "orgId": receipt["org_id"]})
        )
        env = {**os.environ, "SWIFTCHART_BACKEND_ORIGIN": receipt["backend_origin"]}
        raw = cli(
            binary,
            "deploy",
            "--prod",
            "--yes",
            "--skip-domain",
            "--json",
            "--scope",
            SCOPE,
            "--build-env",
            "SWIFTCHART_RELEASE_SHA=" + sha,
            "--build-env",
            "SWIFTCHART_BACKEND_ORIGIN=" + receipt["backend_origin"],
            "--build-env",
            "VITE_API_BASE=",
            cwd=target,
            env=env,
        )
        data = json.loads(raw)
        deployed = data.get("deployment", {})
        if (
            data.get("status") != "ok"
            or deployed.get("readyState") != "READY"
            or not deployed.get("url")
        ):
            raise ValueError("Frontend build did not become ready")
        receipt["staged_deployment"] = {
            "url": deployed["url"],
            "id": deployed.get("id") or deployed.get("uid"),
        }
        state.write_text(json.dumps(receipt, indent=2))
        # No custom-domain switch until the same backend is still healthy.
        backend_ready(sha)
        if current_alias(binary) != receipt["previous"]:
            raise ValueError(
                "Frontend changed during build; refusing to replace someone else’s release"
            )
        cli(binary, "promote", deployed["url"], "--yes", "--scope", SCOPE, env=env)
        cli(binary, "alias", "set", deployed["url"], DOMAIN, "--scope", SCOPE)
        with urlopen(
            "https://" + DOMAIN + "/release.json?release=" + sha, timeout=30
        ) as response:
            if json.load(response) != {"release": sha}:
                raise ValueError("Public frontend release mismatch; rollback required")
        receipt.update(published=True, published_at=datetime.now(UTC).isoformat())
        state.write_text(json.dumps(receipt, indent=2))
    return {"published": True, "release_commit": sha, "domain": DOMAIN}


def rollback(binary, state):
    receipt = json.loads(state.read_text())
    old = receipt["previous"]
    cli(binary, "rollback", old["deployment_id"], "--yes", "--scope", SCOPE)
    cli(binary, "alias", "set", old["url"], DOMAIN, "--scope", SCOPE)
    if current_alias(binary) != old:
        raise ValueError("Frontend rollback alias mismatch")
    receipt["rolled_back_at"] = datetime.now(UTC).isoformat()
    state.write_text(json.dumps(receipt, indent=2))
    return {"rolled_back": True, "deployment_id": old["deployment_id"]}


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("action", choices=["prepare", "deploy", "rollback"])
    p.add_argument("--cli", type=Path, required=True)
    p.add_argument("--state", type=Path, required=True)
    p.add_argument("--commit", default="HEAD")
    p.add_argument("--link", type=Path)
    p.add_argument("--approved", action="store_true")
    a = p.parse_args()
    if a.action != "prepare" and not a.approved:
        raise ValueError("Explicit backend/frontend approval required")
    result = (
        prepare(a.cli, a.state, a.commit, a.link)
        if a.action == "prepare"
        else (
            publish(a.cli, a.state)
            if a.action == "deploy"
            else rollback(a.cli, a.state)
        )
    )
    print(json.dumps(result))
