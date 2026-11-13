# Stage 1 VPS-based backend release

This release uses the preserved VPS backend as its implementation source of truth.
Its publishable Git branch starts from public `origin/main`; the local-only VPS
preservation commit is not pushed or merged. PR #10 is reference material only.
The current public frontend is unchanged and is not part of the runtime payload.

## Decisions implemented

- Keep canonical outbox claiming, eligibility, recipient retries, subscriptions,
  keyboards, BTC context and the existing dispatcher. No second scan loop. A
  successful send records its Telegram timestamp/message identity in the existing
  recipient row; repeated callbacks preserve the first receipt. Tracking reads
  successful canonical receipts, never creation, queue or dedupe timestamps.
- Preserve opportunity keys, strategy versions, retest promotion, production and
  shadow eligibility, stale-lock repair and lifecycle state updates. Add audited
  replay, parent-consistent 15-minute tie-breaking, explicit modeled time exits,
  modeled fees and historical-rate funding estimates around those paths.
- Production statistics exclude shadow, NO_TRADE and pending-retest cohorts.
  Completed entered trades with known modeled costs form the expectancy sample.
  Counts and sample size are explicit; missing net costs return null, never zero.
  Full-target, partial and time-exit outcomes are separate.
- Keep the five-second History availability wrapper. Timeout/failure retains
  saved records and clears uncertified numeric results. Add the paper tests from
  PR #10. Bot history/stats use corrected fields; legacy Supabase paper R without
  cost certification is displayed as unknown rather than presented as net R.
- Retain all VPS settings and add the fee/softening defaults. Live crypto scans
  still reach `analyze_dataframe -> build_trade_ideas`, including the softening
  block, so the default-off NO_TRADE range-softening switch applies there.
- Remove OKX ASP/x402 integration modules, routes, gateway tests and dependency
  declarations at the user's request. Retain the two core analysis regressions
  in `test_market_analysis_service.py`. There are no test exclusions.
- The only Forex runtime change is accepting `ACTIVE_SIGNAL_REUSED` in the scan
  result model, fixing the six observed validation failures. Other Forex source
  is unchanged. An older OANDA test stub was updated to accept the existing VPS
  provider interface; provider behavior itself is unchanged.

The audited `tracking_rules.py` SHA-256 remains
`743872dd9c87c5beee95ed543751bf4870ee871b445e04eeb207c9f2e7b3d048`.

Funding uses historical hourly Hyperliquid rates valued at entry notional. It is
explicitly an estimate: the public history endpoint does not contain historical
oracle prices or wallet debits. Missing/conflicting rates keep net R unknown.
Fees likewise are a model, not authenticated execution costs. No actual-fill or
slippage certification is implied. References:
[funding formula](https://hyperliquid.gitbook.io/hyperliquid-docs/trading/funding),
[funding-history API](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint/perpetuals).

## Validation and limits

The final report records the exact test counts and full-suite log. Run all
Python tests with:

```bash
python scripts/check_stage1.py
```

That runs the complete backend and execution-bot suites without deselections or
known-failure exceptions. Regression coverage exercises canonical
receipts, replay-to-API/bot costs, identity/lock preservation, cohort exclusion,
unknown funding, History timeout, 60-day candle budgets and rollback data safety.

No production data is copied into tests. Read-only VPS inspection confirmed that
both PM2 processes use `/opt/swiftchart`, `/usr/bin/bash`, the existing backend/bot
virtualenvs and the existing `.env` source statement. Nothing was deployed,
migrated, restarted, replayed or published. Production replay coverage, actual
post-deploy stats and bot results remain unverified until the approved deployment.

Sixty-day replay requests sufficient 1h history instead of the old 1,000-bar cap,
bounded by exchange retention. Missing history or ambiguous 15m ordering stays
unresolved. Replay completion does not prove every row was resolved. Historical
signals outside the selected window remain excluded from certified statistics
until separately reconciled. Supabase paper data is not migrated by this release.

## Backend-only payload and scan scope

`stage1_pack.py` packages an immutable commit's API/bot code, replay/admin helpers
and source-baseline fingerprint. It excludes frontend, tests, OKX integration
modules/routes, dependency installation and live environment/data files. The
payload includes a per-file SHA-256 manifest and is verified before use. No new
backend packages are required. OKX source and dependency declarations have been
removed; the payload denylist also prevents accidental reintroduction.

Scan the changed source and entire runtime payload for secrets and hardcoded IPs.
Loopback and bind addresses are allowed. Live env files, private agent paths,
analysis outputs and local preservation metadata must never be staged or packed.
Existing public frontend/prototype files outside this backend release are neither
deployed nor rewritten to claim that a historical repository-wide scan passed.

## Deployment layout — backend approved, frontend remains on hold

The old `/opt/swiftchart` checkout is deliberately preserved. It is dirty and
must not be reset, stashed, cleaned, overwritten or fast-forwarded. Deployment
uses a **new empty release directory**, with the old virtualenvs and database.
This is how the release preserves the running source and allows rollback without
destroying uncommitted VPS work. The baseline guard checks its branch, HEAD,
public origin and captured source hashes before services are stopped. Any drift
stops deployment for review. No existing `.env` is edited or copied.

Replace `APPROVED_SHA` with the full tested commit from the release report. The
user has authorized removal of OKX and backend deployment; record the actual
execution and post-deployment results separately. Frontend stays on hold.

```bash
# On the local release worktree:
set -euo pipefail
release_sha=APPROVED_SHA
payload="/tmp/swiftchart-stage1-${release_sha}.tar.gz"
python3 scripts/stage1_pack.py --ref "$release_sha" --output "$payload"
ssh swiftchart-vps "test ! -e /tmp/swiftchart-stage1-${release_sha}.tar.gz"
scp "$payload" "swiftchart-vps:/tmp/swiftchart-stage1-${release_sha}.tar.gz"
ssh swiftchart-vps bash -s -- "$release_sha" <<'BACKEND_DEPLOY'
set -euo pipefail
umask 077
release_sha="$1"
release_root="/opt/swiftchart-releases/$release_sha"
state="/opt/swiftchart-stage1-state/$release_sha"
test ! -e "$release_root"
test ! -e "$state"
mkdir -m 700 -p "$release_root"
tar -xzf "/tmp/swiftchart-stage1-${release_sha}.tar.gz" -C "$release_root"
old_root=/opt/swiftchart
python="$old_root/backend/.venv/bin/python"
admin="$release_root/scripts/stage1_admin.py"
export PYTHONDONTWRITEBYTECODE=1
"$python" "$admin" verify --root "$release_root" --state "$state" --expected-commit "$release_sha"
set -a
source "$old_root/.env"
set +a
# Verify imports with each existing VPS virtualenv before stopping services.
(cd "$release_root" && PYTHONPATH="$release_root/backend:$release_root" \
  "$python" -c 'from app.main import app')
(cd "$release_root" && PYTHONPATH="$release_root/backend:$release_root" \
  "$old_root/bot/.venv/bin/python" -c 'from bot.main import main')
"$python" "$admin" prepare --root "$release_root" --state "$state"
pm2 stop swiftchart-api swiftchart-bot
if ! "$python" "$admin" backup --root "$release_root" --state "$state"; then
  pm2 restart swiftchart-api swiftchart-bot --update-env
  exit 1
fi
if ! "$python" "$admin" schema --root "$release_root" --state "$state"; then
  "$python" "$admin" rollback --root "$release_root" --state "$state"
  exit 1
fi
# Restart immediately after schema, before replay.
if ! pm2 startOrRestart "$state/release-processes.cjs" \
  --only swiftchart-api,swiftchart-bot --update-env; then
  "$python" "$admin" rollback --root "$release_root" --state "$state"
  exit 1
fi
if ! curl --fail --silent --retry 20 --retry-delay 1 --retry-connrefused \
  http://localhost:8000/health > "$state/health.json"; then
  "$python" "$admin" rollback --root "$release_root" --state "$state"
  exit 1
fi
database_path=$("$python" -c 'import json,sys;print(json.load(open(sys.argv[1]))["database_path"])' "$state/state.json")
export DATABASE_URL="sqlite:///$database_path"
export PYTHONPATH="$release_root/backend"
cd "$release_root"
replay_status=0
"$python" scripts/replay_tracking.py --days 60 --report "$state/replay.json" \
  > "$state/replay-output.json" || replay_status=$?
curl --fail --silent http://localhost:8000/api/trade-stats > "$state/trade-stats.json"
cat "$state/health.json"
cat "$state/replay-output.json"
cat "$state/trade-stats.json"
# Replay report includes elapsed_seconds and coverage/unknown counts.
exit "$replay_status"
BACKEND_DEPLOY
```

Backups use SQLite's online backup API and integrity checks; API/bot are stopped
for the final pre-schema backup. The schema step is idempotent and additive:
initialize the existing canonical schema and create the append-only correction
ledger/index/triggers. It does not run Forex strategy migrations or Supabase SQL.
Data and virtualenv paths remain in the old directory; only API/bot code cwd is
changed. Do not restart the executor or private agent processes. Do not run a
frontend command, Vercel publish or alias update.

Existing settings stay unchanged. New defaults are built into code:

```dotenv
TRACKING_COST_BPS_PER_SIDE=5
ALLOW_NO_TRADE_RANGE_SOFTENING=false
CRYPTO_LIFECYCLE_ENABLED=true
CRYPTO_LIFECYCLE_INTERVAL_SECONDS=120
CRYPTO_LIFECYCLE_STARTUP_DELAY_SECONDS=10
```

## Rollback

The state receipt records the previous commit, exact previous source directory,
database path and whitelisted PM2 commands, without copying environment secrets.
Because the previous checkout is dirty, checking out its old HEAD would discard
real deployed behavior. Rollback therefore restores that **untouched previous
working tree**, not an incomplete clean version of its historical commit.

```bash
ssh swiftchart-vps bash -s -- APPROVED_SHA <<'BACKEND_ROLLBACK'
set -euo pipefail
release_sha="$1"
release_root="/opt/swiftchart-releases/$release_sha"
state="/opt/swiftchart-stage1-state/$release_sha"
/opt/swiftchart/backend/.venv/bin/python "$release_root/scripts/stage1_admin.py" \
  rollback --root "$release_root" --state "$state"
curl --fail --silent --retry 20 --retry-delay 1 --retry-connrefused http://localhost:8000/health
BACKEND_ROLLBACK
```

Rollback stops API/bot, preserves the post-deployment database as
`before-rollback.db`, restores `before.db` with SQLite backup/integrity checks and
restarts the original PM2 cwd/commands. Existing rollback snapshots are not
overwritten. Source, old checkout, environment, data archives and release/state
directories are retained; nothing is deleted. Post-deployment writes are preserved
in the archive for reconciliation even though the active DB is rewound.

After deployment return the exact `/api/trade-stats`, 60-day replay report and
duration before requesting separate frontend approval. No manual bot message is
authorized by the backend release; an operator can supply `/stats` verification.
