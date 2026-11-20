#!/usr/bin/env python3
"""Reject staged private files and non-placeholder environment templates."""
from pathlib import PurePosixPath
import re
import subprocess
import sys

names = subprocess.check_output([
    "git", "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z"
]).split(b"\0")
blocked_dirs = {
    "analysis_outputs", "variational_rewards", "personal_data", "private_data",
    "user_data", "customer_data", "wallet_tracker", "exports", "logs", ".security-cleanup",
}
errors = []
for raw in names:
    if not raw:
        continue
    path = raw.decode("utf-8", "surrogateescape")
    parts = PurePosixPath(path).parts
    name = parts[-1].lower()
    forbidden = bool(set(p.lower() for p in parts) & blocked_dirs)
    forbidden |= (name == ".env" or name.startswith(".env.") or name.endswith(".env")) and name != ".env.example"
    forbidden |= bool(re.search(r"\.(?:db|sqlite|sqlite3)(?:$|[-.])", name))
    forbidden |= bool(re.search(r"\.(?:pem|key|p12|pfx|jks|keystore|ppk|dump)$", name))
    forbidden |= bool(re.fullmatch(r"id_(?:rsa|dsa|ecdsa|ed25519)(?:\.pub)?", name))
    forbidden |= name in {"credentials", ".netrc", ".npmrc", ".pypirc", "authorized_keys", "known_hosts"}
    forbidden |= bool(re.search(r"\.log(?:$|\.)", name))
    if forbidden:
        errors.append(f"{path}: private/generated file must not be committed")
        continue
    if name == ".env.example":
        data = subprocess.check_output(["git", "show", ":" + path]).decode("utf-8")
        for number, line in enumerate(data.splitlines(), 1):
            text = line.strip()
            if not text or text.startswith("#"):
                continue
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=YOUR_[A-Za-z0-9_]+", text):
                errors.append(f"{path}:{number}: template values must be YOUR_* placeholders")
if errors:
    print("Commit blocked by staged-file security checks:", file=sys.stderr)
    print("\n".join(errors), file=sys.stderr)
    sys.exit(1)
