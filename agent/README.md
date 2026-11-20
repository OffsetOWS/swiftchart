# Agent Swift

This directory contains only the Telegram agent, Hermes/Bedrock and news-only
Grok clients, the HTTP tool adapter, conversation JSON store, and tests.
There is no backend copy, exchange connection, agent SQLite store, or trading
database in the agent image. The canonical backend remains under `backend/`.

Run with `PYTHONPATH=agent python -m telegram_bot.main`. The source directory is
intentionally not a Python `agent` package: that name belongs to Hermes.

Supply the existing Telegram token and unchanged user/chat allowlist through
the service environment. Empty allowlists deny access. Do not start a second
poller for the same token. Bedrock uses Hermes with the explicit project Region,
existing configured model and a response token limit. Grok is requested only for
news/macro questions. Model output cannot replace API prices, levels or statuses;
numeric market replies use a deterministic API-field formatter. Aggregate
performance requests are refused and their fields are absent from tool output.

All eight tools use GET requests against `/internal/agent/` on the loopback
SwiftChart origin. Two HTTP requests may be active, identical requests coalesce,
and cache lifetime is capped at twenty seconds. Errors never invent fallback
prices or signals. Unsupported symbols return the current supported market list.

Conversations are bounded private JSON files outside the source tree; the API's
trading database is never mounted into the agent. Existing market databases,
Hermes session databases, and conditional-alert workers are not imported.

Use the reviewed deployment and rollback runbook. The service must use a
read-only container filesystem, CPU and memory limits, loopback API access,
private environment files and separate session/log mounts. Block `/internal/`
at nginx in addition to the API's token, loopback-peer and proxy-header checks.

New developer clones must install Gitleaks and run
`sh scripts/install_security_hooks.sh` at the repository root.
