"""Build a manifest-pinned backend payload locally. This never contacts the VPS."""

import argparse, hashlib, io, json, subprocess, tarfile
from pathlib import Path
from scripts.crypto_soak_gate import included, fingerprint, validate

RULES_HASH = "743872dd9c87c5beee95ed543751bf4870ee871b445e04eeb207c9f2e7b3d048"


def pack(root, ref, output, soak_report):
    def git(*args):
        return subprocess.check_output(["git", "-C", str(root), *args])

    sha = git("rev-parse", ref + "^{commit}").decode().strip()
    files = []
    rules = git("show", sha + ":backend/app/services/tracking_rules.py")
    if hashlib.sha256(rules).hexdigest() != RULES_HASH:
        raise ValueError("Audited tracking rules changed")
    for raw in git("ls-tree", "-r", "-z", sha).split(b"\0"):
        if not raw:
            continue
        info, name = raw.split(b"\t", 1)
        mode, kind, oid = info.decode().split()
        name = name.decode()
        if not included(name):
            continue
        if kind != "blob" or mode not in {"100644", "100755"}:
            raise ValueError("Unsupported payload path")
        files.append(
            (name, git("cat-file", "blob", oid), 0o755 if mode == "100755" else 0o644)
        )
    manifest = {
        "release_commit": sha,
        "dependency_hash": hashlib.sha256(
            git("show", sha + ":backend/requirements.txt")
        ).hexdigest(),
        "files": {name: hashlib.sha256(body).hexdigest() for name, body, _ in files},
        "excluded": [
            "frontend",
            "tests",
            "live environment/data",
            "dependency installation",
        ],
    }
    manifest["soak"] = validate(json.loads(soak_report.read_text()), sha, fingerprint(manifest["files"]))
    with output.open("xb") as stream, tarfile.open(
        fileobj=stream, mode="w:gz"
    ) as archive:
        for name, body, mode in files + [
            ("release-manifest.json", json.dumps(manifest, indent=2).encode(), 0o600)
        ]:
            entry = tarfile.TarInfo(name)
            entry.mode = mode
            entry.size = len(body)
            archive.addfile(entry, io.BytesIO(body))
    return manifest


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--ref", required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--soak-report", type=Path, required=True)
    a = p.parse_args()
    result = pack(Path(__file__).resolve().parents[1], a.ref, a.output, a.soak_report)
    print(
        json.dumps(
            {
                "release_commit": result["release_commit"],
                "file_count": len(result["files"]),
                "output": str(a.output),
            }
        )
    )
