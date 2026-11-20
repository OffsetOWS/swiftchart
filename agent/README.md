# Agent Swift

This directory contains only the Telegram agent, Hermes/Bedrock and news-only
Grok clients, the HTTP tool adapter, conversation JSON store, and tests.
There is no backend copy, exchange connection, agent SQLite store, or trading
database in the agent image. The canonical backend remains under `backend/`.

Market answers have a four-line default and a detailed view with both timeframes,
upside/downside scenarios and invalidation. Hermes writes from the API's display
facts; numerical and semantic checks fall back to the API template on failure.
The default uses cached higher-timeframe context without queuing extra provider
reads. Unavailable context is described plainly; detailed requests fetch it.

Each market reply carries Chart, More detail, timeframe, News and Alert me
buttons. Button tokens belong to that chat and user, expire after seven days,
and preserve the original reply's market context. Follow-ups reuse the latest
symbol and timeframe. Signal alerts deep-link using a canonical signal ID.
Charts contain API candles, API-formatted axes and levels, with a short cache.
No chart renderer or agent tool calculates market prices.

Price alerts are user-requested notifications: ten active per user, seven-day
expiry, five-second polling using fresh API quotes. `/alerts` lists and cancels
them. State is owner-only JSON outside Git. A hit is claimed before sending;
ambiguous Telegram delivery is recorded and never automatically retried, because
Telegram has no idempotency key. This prevents duplicate notifications after a
restart, though a network failure can leave delivery uncertain. No trades are
placed. Stale or unavailable quotes cannot create or trigger an alert.

Unverified Variational candle coverage is excluded from the catalogue and new
analysis/scans. Enable it only after verifying the candle feed, with both
`VARIATIONAL_ENABLED` and `VARIATIONAL_CANDLES_VERIFIED`. Existing tracking and
Forex strategy rules are unchanged. Provider identifiers retain their original
case even though public symbols are normalized.

Internal loopback/token-protected reads now also include `/prices`,
`/chart/{symbol}`, `/signal-context/{market_type}/{identity}` and the snapshot's
`detail` parameter. They must remain inaccessible through the public proxy.

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
