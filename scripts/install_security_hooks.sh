#!/bin/sh
set -eu
cd "$(git rev-parse --show-toplevel)"
command -v gitleaks >/dev/null 2>&1 || {
    echo "Install Gitleaks first (macOS: brew install gitleaks)." >&2
    exit 1
}
command -v python3 >/dev/null 2>&1 || {
    echo "Python 3 is required for staged-file checks." >&2
    exit 1
}
git config --local core.hooksPath .githooks
echo "Security pre-commit hook installed for this clone."
