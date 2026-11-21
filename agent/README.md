# Agent Swift

This directory contains only the Telegram agent, configurable Grok writers, optional
Hermes/Bedrock fallback and news search clients, the HTTP tool adapter, conversation JSON store, and tests.
There is no backend copy, exchange connection, agent SQLite store, or trading
database in the agent image. The canonical backend remains under `backend/`.

Market answers have a four-line default and a detailed view with both timeframes,
upside/downside scenarios and invalidation. Grok writes from the API's display
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
poller for the same token. The default writer is xAI and requires only the existing
server `XAI_API_KEY`. Bedrock is lazy and off unless explicitly selected or enabled
as a fallback. News alone can invoke X/Web Search; market writers have no tools.
Model output cannot replace API prices, levels or statuses;
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

Writer and usage settings (service environment; restart only the agent to apply):

| Setting | Default |
| --- | --- |
| TRADING_AGENT_WRITER_PROVIDER | xai |
| TRADING_AGENT_WRITER_DEFAULT_MODEL | grok-4.20-0309-non-reasoning |
| TRADING_AGENT_WRITER_DETAIL_MODEL | grok-4.3 |
| TRADING_AGENT_WRITER_DETAIL_REASONING_EFFORT | none (empty omits the field) |
| TRADING_AGENT_BEDROCK_FALLBACK_ENABLED | false |
| TRADING_AGENT_DAILY_QUESTION_LIMIT | 30 |
| TRADING_AGENT_DAILY_LIMIT_TIMEZONE | Africa/Lagos |
| TRADING_AGENT_NEWS_CACHE_SECONDS | 900 |
| TRADING_AGENT_X_SEARCH_POST_LIMIT | 8 (cannot exceed 8) |

`TRADING_AGENT_GROK_MODEL` selects the news model independently. News summaries
coalesce and cache per canonical API symbol for fifteen minutes. The prompt asks
for one X keyword/semantic query limited to eight posts, no profile/thread fetches;
only eight X post sources are retained. The provider does not offer a hard fetch
cap. Log `x_posts_fetched` and `x_overfetch` when returned; do not describe this as
a guaranteed billing cap. Repeated cached news has no new provider charge.

Thirty questions per user per Lagos calendar day includes text questions, signal
opens and detail/timeframe/news requests. Chart and alert-management buttons are
exempt. Quotas persist as owner-only JSON across chats, reset commands and process
restarts. Empty allowlists still deny access; no identity is sent to a model.

Owner-only `usage/*.jsonl` beside the session directory records a random question
ID, kind, model, token usage, exact provider-reported billed cost, reply duration
and X over-fetching. It never records prompts, replies, recipient IDs or keys.
The xAI `usage.cost_in_usd_ticks` receipt includes tools and caching; divide by
10^10 for USD. Missing receipts/timeouts and Bedrock costs remain unknown, never
zero. No-provider/cache hits cost zero. A shielded call completing after a reply
gets a `question_usage_update` with its eventual cost rather than losing the bill.
Private daily logs rotate at five MB; conversation/usage/quota state stays out of Git.
