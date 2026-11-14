# Stage 2 backend release

Use PR #11's deployed commit `1562fa2162e1c6619fcebed1425cf712f208ad9e` as the previous release. Stage 2 is prepared for owner approval; these commands must not run before the explicit backend deployment yes.

The owner-only report holds the measured delivery baseline, admission/volume evidence, gross score-band study and frontend inventory. It stays local. No historical trade results are published in this runbook or bot messages.

## Changes

- `/stats` returns an unavailable notice without querying statistics. History and paper messages omit realized R, PnL and cost results. The internal `/api/trade-stats` API stays unchanged. Setup scores, prospective levels and trade status remain available.
- The existing scheduled crypto worker drains the same canonical outbox every 30 seconds, without the old five-minute minimum or 30-minute environment setting. No new market-scan loop. The dedicated Forex loop and Forex modules are unchanged.
- Blocked, missing-chat, deactivated and forbidden recipients become terminal crypto failures, with a canonical recipient quarantine. New opportunities skip quarantined recipients. Explicit `/subscribe` clears the future-work quarantine without retrying old terminal receipts.
- Completed partial dispatches stop occupying the retry queue. Retry-after deadlines and stale leases are checked before the claim limit. Successful receipts remain idempotent and retain their original Telegram date/message identity.
- Crypto defaults: three signals per scan invocation across its selected exchanges, at most two per direction, and a $250,000 reported 24-hour volume minimum. Preserve existing ranking and shadow/retest/rejected cohorts. Manual crypto entry points honor these gates as well.
- No score weight, audited replay, lifecycle-lock, fee, funding or Forex strategy change. No frontend files or publication. Existing dependencies and `.env` remain in place.

The cap is per `run_scan(exchange,timeframe)` invocation, rather than a rolling global rate. It is also applied to manual crypto requests. Public input values are guarded; missing/non-finite volume is rejected.

## Validation

```bash
python scripts/check_stage1.py
npm --prefix frontend test
```

The historical runner filename executes the complete backend and execution-bot suites with no exclusions. The final owner report records the exact counts, source hashes and secret/IP scan. Native PM2 JSON validation runs before any process is stopped, and actual process directories are checked on activation.

## Reviewed defaults

```dotenv
CRYPTO_MAX_SIGNALS_PER_SCAN=3
CRYPTO_MAX_SIGNALS_PER_DIRECTION=2
MIN_PERP_VOLUME_24H=250000
CRYPTO_TELEGRAM_DISPATCH_INTERVAL_SECONDS=30
```

The release helper explicitly applies only these four numeric defaults after sourcing the unchanged old `.env`. This matters because the existing environment contains the previous volume floor and legacy interval. Database and bot state exports from the running process commands are preserved, not reconstructed from relative paths. No fee/funding settings are changed.

## Deployment — after owner approval

Replace `APPROVED_SHA` with the full tested candidate commit. Run from the local PR #11 worktree. The payload excludes frontend, tests, dependency installation, live environment/data and removed integrations.

```bash
set -euo pipefail
release_sha=APPROVED_SHA
payload="/tmp/swiftchart-stage2-${release_sha}.tar.gz"
python3 scripts/stage1_pack.py --ref "$release_sha" --output "$payload"
ssh swiftchart-vps "test ! -e /tmp/swiftchart-stage2-${release_sha}.tar.gz"
scp "$payload" "swiftchart-vps:/tmp/swiftchart-stage2-${release_sha}.tar.gz"
ssh swiftchart-vps bash -s -- "$release_sha" <<'STAGE2_DEPLOY'
set -euo pipefail
umask 077
release_sha="$1"
root="/opt/swiftchart-releases/$release_sha"
state="/opt/swiftchart-stage2-state/$release_sha"
previous_root=/opt/swiftchart-releases/1562fa2162e1c6619fcebed1425cf712f208ad9e
previous_state=/opt/swiftchart-stage1-state/1562fa2162e1c6619fcebed1425cf712f208ad9e
python=/opt/swiftchart/backend/.venv/bin/python
test ! -e "$root"
test ! -e "$state"
mkdir -m 700 -p "$root"
tar -xzf "/tmp/swiftchart-stage2-${release_sha}.tar.gz" -C "$root"
export PYTHONDONTWRITEBYTECODE=1
set -a
source /opt/swiftchart/.env
set +a
(cd "$root" && PYTHONPATH="$root/backend:$root" "$python" -c 'from app.main import app')
(cd "$root" && PYTHONPATH="$root/backend:$root" /opt/swiftchart/bot/.venv/bin/python -c 'from bot.main import main')
admin="$root/scripts/stage2_admin.py"
"$python" "$admin" prepare --root "$root" --previous-root "$previous_root" \
  --previous-state "$previous_state" --state "$state" --expected-commit "$release_sha"
pm2 stop swiftchart-api swiftchart-bot > "$state/pm2-stop.log"
if ! "$python" "$admin" backup --root "$root" --state "$state"; then
  pm2 restart swiftchart-api swiftchart-bot --update-env > "$state/backup-recovery.log"
  exit 1
fi
if ! "$python" "$admin" schema --root "$root" --state "$state" > "$state/schema.json"; then
  "$python" "$admin" rollback --root "$root" --state "$state" > "$state/rollback.log"
  exit 1
fi
# Restart immediately after schema and known-failure migration.
if ! "$python" "$admin" activate --root "$root" --state "$state" > "$state/activate.log"; then
  "$python" "$admin" rollback --root "$root" --state "$state" > "$state/rollback.log"
  exit 1
fi
if ! curl --fail --silent --retry 60 --retry-delay 1 --retry-connrefused \
  http://localhost:8000/health > "$state/health.json"; then
  "$python" "$admin" rollback --root "$root" --state "$state" > "$state/rollback.log"
  exit 1
fi
"$python" "$admin" verify --root "$root" --state "$state"
if ! curl --fail --silent --max-time 120 http://localhost:8000/api/trade-stats \
  > "$state/internal-trade-stats.json"; then
  "$python" "$admin" rollback --root "$root" --state "$state" > "$state/rollback.log"
  exit 1
fi
sleep 5
"$python" "$admin" verify --root "$root" --state "$state"
pm2 save > "$state/pm2-save.log"
cat "$state/health.json"
cat "$state/schema.json"
STAGE2_DEPLOY
```

Preflight requires the immutable previous manifest and actual API/bot process directories to match. It refuses an existing release/state directory and copies only whitelisted process definitions, never environment values. The original dirty checkout is not reset, stashed, cleaned or overwritten. Shared virtualenvs, bot state and the live SQLite path stay unchanged.

The schema step adds `next_attempt_at`, the crypto recipient quarantine table and a recipient lookup index. It terminalizes only known permanent, unsent crypto failures; successful receipts and all trade/cohort identities remain intact. It does not run a trade replay or any Forex/strategy migration. No fee/funding requests are part of this deployment.

## Rollback

`before.db` is an integrity-checked SQLite backup made with API/bot stopped. Rollback first archives post-deploy writes as `before-rollback.db`, restores the backup and recreates the saved previous API/bot definitions pointing to commit `1562fa2162e1c6619fcebed1425cf712f208ad9e`. It handles absent roles after partial activation. Source, environment and archives remain intact; existing rollback archives are never overwritten. The executor is never selected.

```bash
ssh swiftchart-vps bash -s -- APPROVED_SHA <<'STAGE2_ROLLBACK'
set -euo pipefail
release_sha="$1"
root="/opt/swiftchart-releases/$release_sha"
state="/opt/swiftchart-stage2-state/$release_sha"
/opt/swiftchart/backend/.venv/bin/python "$root/scripts/stage2_admin.py" \
  rollback --root "$root" --state "$state"
curl --fail --silent --retry 60 --retry-delay 1 --retry-connrefused http://localhost:8000/health
pm2 save
STAGE2_ROLLBACK
```

After deployment, read the same canonical receipt metric for newly eligible opportunities. Compare creation-to-delivery and eligibility-to-first-claim separately; include pending/failed deliveries rather than treating them as zero latency. A 30-second poll is not proof of a two-minute delivery guarantee during provider outages or backlogs. Do not send a manual Telegram test message or publish the frontend as part of this release.
