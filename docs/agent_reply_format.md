# API-grounded Agent Swift replies

Market snapshots are composed by the Bedrock model through Hermes in five lines:

1. Symbol, timeframe, plain-language bias and published signal status.
2. Current price, trend and higher-timeframe direction.
3. Nearest support and resistance, with their location relative to price.
4. One action: wait for confirmation or monitor an active published signal.
5. API observation time and “Not financial advice.”

The internal API supplies `reply` display facts and allowed line wordings. It
formats Forex to five decimal places, JPY pairs to three, and gold to two.
Crypto precision comes from provider metadata. Hyperliquid uses its documented
perpetual price rules (five significant figures and at most six minus the asset's
size-decimal places; integer prices are allowed). If provider precision is
unknown, prices are unavailable rather than rounded to a guessed tick.
See [Hyperliquid tick and lot sizes](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/tick-and-lot-size).

The agent performs no price arithmetic. After composition it verifies every
numeric token against the API display facts, then checks each line against the
allowed wording for that position. This also rejects swapped support/resistance,
incorrect signal status and extra text. Any failure or provider outage uses the
plain API template. Identical fact blocks coalesce model calls for a short time;
a busy provider uses the same fallback without an unbounded queue. Conversation
history is excluded from this factual writer. Qualitative news uses Grok only
for explicit news questions; unverified numerical claims are rejected.

No hypothetical confirmation, stop or target levels are displayed on a no-trade.
Only persisted signals determine whether a signal is active. The stats refusal
is: “I don't share performance stats. Ask me about a specific coin or signal.”

Crypto swing roles are checked against price before the strength cap, in the
shared calculation used by the API scanner, website and manual signal-bot
analysis. Fully broken bands are dropped; the code does not invent a confirmed
role reversal. A band containing price remains valid touch/retest structure and
is explicitly described as containing price. Nearest-zone selection has no
wrong-side fallback. These changes can change new signal eligibility; existing
trade tracking rules and the Forex completed-candle zone engine are unchanged.
Forex display projections omit wrong-side or terminal zones without treating a
forming quote as a completed structural reversal.

The offline release soak exercises the actual API and agent's provider-unavailable
fallback with outbound networking blocked. Separate model previews verify real
composition. An offline fallback pass is not a live-provider throughput test.
Use `--agent-crypto-metadata` with a private captured Hyperliquid `meta` response
when running `--agent-questions 100`. The load check requires formatted prices,
so unknown precision or unavailable quotes cannot count as successful answers.
Every deployment still requires the exact committed candidate's full gate in
`docs/crypto_release_gate.md`, followed by explicit deployment approval.
