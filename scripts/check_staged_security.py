#!/usr/bin/env python3
"""Reject staged private files and environment templates that carry real values.

Run with no arguments from the pre-commit hook (checks staged files), or with
``--tracked`` in CI (checks every tracked file at HEAD).

Environment templates (``.env.example``) may contain harmless defaults such as
``APP_NAME=SwiftChart`` or ``ALERTS_ENABLED=true``. Credential-like keys must be
empty or a ``YOUR_*`` placeholder, and no value may contain credentials in a
URL, a raw IP address, or a long opaque token.
"""
from __future__ import annotations

import ipaddress
from pathlib import PurePosixPath
import re
import subprocess
import sys

BLOCKED_DIRS = {
    "analysis_outputs", "variational_rewards", "personal_data", "private_data",
    "user_data", "customer_data", "wallet_tracker", "exports", "logs", ".security-cleanup",
}

# Key names whose value is a credential or a personal identifier.
SENSITIVE_KEY = re.compile(
    r"(?:TOKEN|SECRET|PASSWORD|PASSWD|PRIVATE|CREDENTIAL|JWT|MNEMONIC|SEED_PHRASE"
    r"|(?:^|_)(?:API_)?KEY$|ANON_KEY|_ADMIN_ID$|_USER_IDS?$|_CHAT_IDS?$|WALLET_ADDRESS)",
)
PLACEHOLDER = re.compile(r"YOUR_[A-Za-z0-9_]+")
ASSIGNMENT = re.compile(r"(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)")
URL_WITH_USERINFO = re.compile(r"[a-z][a-z0-9+.-]*://[^/\s@]+@", re.IGNORECASE)
IPV4 = re.compile(r"(?<![\d.])(\d{1,3}(?:\.\d{1,3}){3})(?![\d.])")
TELEGRAM_TOKEN = re.compile(r"\b\d{5,20}:[A-Za-z0-9_-]{35}\b")
OPAQUE_TOKEN = re.compile(r"[A-Za-z0-9+/_=-]{32,}")
# Loopback / unspecified addresses are fine in templates.
ALLOWED_IPS = {"127.0.0.1", "0.0.0.0"}


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1]
    return value.split(" #", 1)[0].strip()


def check_env_template(path: str, text: str) -> list[str]:
    errors: list[str] = []
    for number, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = ASSIGNMENT.fullmatch(stripped)
        if not match:
            errors.append(f"{path}:{number}: expected KEY=value")
            continue
        key, value = match.group(1), _unquote(match.group(2))
        where = f"{path}:{number}: {key}"
        if SENSITIVE_KEY.search(key):
            if value and not PLACEHOLDER.fullmatch(value):
                errors.append(f"{where} is credential-like; leave it empty or use a YOUR_* placeholder")
            continue
        if not value or PLACEHOLDER.fullmatch(value):
            continue
        if URL_WITH_USERINFO.search(value):
            errors.append(f"{where} contains credentials inside a URL")
        for candidate in IPV4.findall(value):
            try:
                ipaddress.IPv4Address(candidate)
            except ValueError:
                continue
            if candidate not in ALLOWED_IPS:
                errors.append(f"{where} contains a server IP address ({candidate}); use a hostname placeholder")
        if TELEGRAM_TOKEN.search(value):
            errors.append(f"{where} looks like a Telegram bot token")
        elif "://" not in value and "/" not in value and OPAQUE_TOKEN.search(value):
            errors.append(f"{where} looks like an opaque secret token")
    return errors


def check_path(path: str) -> str | None:
    """Return an error if this path must never be committed."""
    parts = PurePosixPath(path).parts
    name = parts[-1].lower()
    forbidden = bool({p.lower() for p in parts} & BLOCKED_DIRS)
    forbidden |= (name == ".env" or name.startswith(".env.") or name.endswith(".env")) and name != ".env.example"
    forbidden |= bool(re.search(r"\.(?:db|sqlite|sqlite3)(?:$|[-.])", name))
    forbidden |= bool(re.search(r"\.(?:pem|key|p12|pfx|jks|keystore|ppk|dump)$", name))
    forbidden |= bool(re.fullmatch(r"id_(?:rsa|dsa|ecdsa|ed25519)(?:\.pub)?", name))
    forbidden |= name in {"credentials", ".netrc", ".npmrc", ".pypirc", "authorized_keys", "known_hosts"}
    forbidden |= bool(re.search(r"\.log(?:$|\.)", name))
    return f"{path}: private/generated file must not be committed" if forbidden else None


def _git_names(tracked: bool) -> list[str]:
    if tracked:
        raw = subprocess.check_output(["git", "ls-files", "-z"])
    else:
        raw = subprocess.check_output(["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z"])
    return [item.decode("utf-8", "surrogateescape") for item in raw.split(b"\0") if item]


def _git_content(path: str, tracked: bool) -> str:
    ref = f"HEAD:{path}" if tracked else f":{path}"
    return subprocess.check_output(["git", "show", ref]).decode("utf-8")


def run(tracked: bool = False) -> list[str]:
    errors: list[str] = []
    for path in _git_names(tracked):
        problem = check_path(path)
        if problem:
            errors.append(problem)
            continue
        if PurePosixPath(path).name.lower() == ".env.example":
            errors.extend(check_env_template(path, _git_content(path, tracked)))
    return errors


def main(argv: list[str]) -> int:
    tracked = "--tracked" in argv
    errors = run(tracked=tracked)
    if errors:
        label = "tracked-file" if tracked else "staged-file"
        print(f"Blocked by {label} security checks:", file=sys.stderr)
        print("\n".join(errors), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
