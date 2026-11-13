# Stage 1 tracking release — approval required before deployment

Prepared November 13, 2026. Public branch: `codex/stage1-tracking`.
The source checkout contains private agent history; this branch instead starts at
public `origin/main` (`ed5218633ebbdaedbe05d4faf094a400b0d36220`).
No private agent paths or commits are included. No VPS commands, restarts,
production replay, environment changes or frontend publication have been run.

## Numbered work report

| Step | Changes and evidence | Verification limits |
| --- | --- | --- |
| 1 | Original full backend suite: 792 passed, 370 warnings. Added API schema coverage for unresolved/expired outcomes. Final isolated public suite: 257 passed, 337 warnings. | The original suite includes private/research tests that deliberately stay outside this public branch. Warnings concern existing FastAPI and pandas deprecations. |
| 2 | Public-only branch and draft PR #10, with separate commits for baseline tracking, time exits, delivery receipts, statistics, funding, intrabar checks, paper consumers and the periodic tracker. Secret/environment/IP checks ran before each commit. | GitHub connector returned 403; authenticated `gh` created the PR successfully. Nothing has been merged. |
| 3 | A separate `tracking_time_exit.py` liquidates remaining modeled quantity at the final tracking candle's close. It preserves partial fills and explicitly records `MODELED_TIME_EXIT`. | This is a new modeled liquidation policy, not evidence of an exchange fill. Missing candles or ambiguous entries remain unresolved. |
| 4 | Successful Telegram messages record exact signal, recipient, message and UTC delivery time. The tracker accepts legacy canonical receipts too. Failed sends are not evidence. All-failed attempts do not set the Telegram dedupe marker. | Historical delivery cannot be invented from creation, queue or local dedupe times. Production receipt coverage remains unknown until replay. A crash between Telegram accepting a message and persisting its receipt still needs reconciliation. |
| 5 | Full-target, partial and other time-exit outcomes have separate counts. Expectancy and grouped results use completed validated entries with known modeled costs. The denominator and excluded/legacy counts are explicit. Empty net samples produce null, not zero. | Open, ambiguous, unentered and unreplayed legacy rows are excluded from expectancy. The aggregate is a completed sample, not an estimate for unresolved positions. |
| 6 | Historical hourly Hyperliquid rates feed signed, quantity-weighted funding estimates. Missing/wrong/conflicting rates leave net R unknown. Raw rates and cost basis persist in the correction ledger. A read-only BTC probe returned three records with the expected schema. | Funding history lacks historical oracle prices. Valuation uses entry notional as an explicit estimate. These are modeled fees plus estimated funding, not actual wallet debits or complete execution/slippage accounting. Other venues' funding remains unknown. |
| 7 | Exact proposed commands and schema/environment changes are below. Backend/API, bot and saved-paper consumers are prepared on the public branch. | Awaiting the user's explicit yes. Production filesystem, branch, DB path, PM2 process names and environment have not been inspected in this run. |
| 8 | Replay maintenance CLI and consistent API/bot/frontend cost fields are prepared. | Not performed: requires approved deployment. Production site/bot verification remains pending. |
| 9 | Local databases were inspected read-only: zero Forex signals exist in the local Forex table. | Zero locally verified resolved Forex trades; production count is unknown. The separate Forex implementation and its swap/spread costs still require work after the deployment gate. No conclusion is drawn from this local sample. |
| 10–25 | Not started in this release. | Dispatcher/caps/volume, weight fitting, replacement research, infrastructure and the Pine/agent comparison remain outstanding. |

Additional verification: 38 frontend tests passed and the production frontend
build passed. Frontend totals use full precision before display rounding and show
the costed sample. Legacy and unknown net values cannot fall back to old gross R.
Saved paper plans now use the same audited replay and cost policy, with save time
as availability evidence; saving a plan does not establish a fill.

The audited `backend/app/services/tracking_rules.py` is byte-for-byte unchanged
from the supplied checkout. SHA-256:
`743872dd9c87c5beee95ed543751bf4870ee871b445e04eeb207c9f2e7b3d048`.
15-minute refinement additionally requires complete children that reproduce the
parent OHLC; mismatched feeds cannot settle an ambiguous result.

Funding convention: modeled entry is included and exits at a settlement timestamp
are applied before funding at that timestamp. Finer replay retains its fine fill
times for quantity calculations. This discrete OHLC convention cannot recover
the true intrabar timing of a real position.

References: [Hyperliquid funding formula](https://hyperliquid.gitbook.io/hyperliquid-docs/trading/funding)
and [public funding-history API](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint/perpetuals).

## Proposed deployment commands — do not execute until approved

First inspect the target read-only and confirm `/opt/swiftchart`, its configured
database and the existing PM2 names. If it has private history, uncommitted work,
different process names or a non-fast-forward deployment branch, stop and prepare
a target-specific patch. Do not reset, clean or delete anything.

Use a reviewed immutable commit from this PR. The following is the exact sequence
for a clean public deployment checkout whose HEAD is an ancestor of that commit.
Replace `REVIEWED_COMMIT` with the approved PR commit SHA before executing.

```bash
cd /opt/swiftchart
test -z "$(git status --porcelain --untracked-files=no)"
git fetch origin codex/stage1-tracking
reviewed_commit=REVIEWED_COMMIT
test "$(git rev-parse origin/codex/stage1-tracking)" = "$reviewed_commit"
git merge-base --is-ancestor HEAD "$reviewed_commit"
previous_commit=$(git rev-parse HEAD)
backup_dir="/opt/swiftchart-backups/stage1-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -m 700 -p "$backup_dir"
printf '%s\n' "$previous_commit" > "$backup_dir/source-before.txt"
set -a
source /opt/swiftchart/.env
set +a
pm2 stop swiftchart-api swiftchart-bot
export TRACKING_BACKUP_DIR="$backup_dir"
PYTHONPATH=backend ./backend/.venv/bin/python - <<'PY'
import os, sqlite3
from pathlib import Path
from app.utils.database import _sqlite_path
path = _sqlite_path().resolve()
if not path.is_file():
    raise SystemExit('Configured database is missing; stop deployment')
source = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
target = sqlite3.connect(Path(os.environ['TRACKING_BACKUP_DIR']) / 'swiftchart-before.db')
with target:
    source.backup(target)
target.close()
source.close()
PY
git merge --ff-only "$reviewed_commit"
PYTHONPATH=backend ./backend/.venv/bin/python - <<'PY'
from app.utils.database import init_db, get_connection
from app.services.tracking_ledger import ensure_tracking_ledger
init_db()
with get_connection() as connection:
    ensure_tracking_ledger(connection)
PY
PYTHONPATH=backend ./backend/.venv/bin/python scripts/replay_tracking.py \
  --days 365 --report "$backup_dir/replay.json"
pm2 restart swiftchart-api swiftchart-bot --update-env
curl --fail --silent http://localhost:8000/health
curl --fail --silent http://localhost:8000/api/trade-stats
```

No package additions are required by the backend change. Do not restart the
executor or any private trading-agent process. If any command fails, stop the
sequence; never continue with a half-applied migration. The source SHA and SQLite
backup are retained for a separately reviewed recovery; no destructive rollback
or database replacement is authorized by this runbook.

Schema additions are idempotent and additive:

- `crypto_delivery_receipts`, keyed by recipient/message, with a signal/time index.
- `tracking_corrections`, its per-signal index and append-only update/delete guards.
- No existing columns, tables, signals or paper records are deleted. Corrections
  and current outcomes are written in the same transaction. Paper corrections
  use a separate `PAPER` asset key and never enter the crypto signal statistics.

Defaults below are new settings. Existing `.env` can remain unchanged because
these defaults are built into configuration. Any requested override requires the
same explicit approval as deployment; no environment secrets need to change.

```dotenv
TRACKING_COST_BPS_PER_SIDE=5
ALLOW_NO_TRADE_RANGE_SOFTENING=false
CRYPTO_LIFECYCLE_ENABLED=true
CRYPTO_LIFECYCLE_INTERVAL_SECONDS=120
CRYPTO_LIFECYCLE_STARTUP_DELAY_SECONDS=10
```

The final frontend must be published separately after backend health and replay
coverage are reviewed. The existing release script builds, publishes and verifies
the public custom domain, so running it is a deployment action requiring approval:

```bash
# Run in the clean checkout already verified at the approved source commit.
git switch -c codex/mobile-only-stage1-tracking-release "$reviewed_commit"
bash scripts/deploy_frontend.sh
```

After approval and deployment, compare `/api/trade-stats` with the bot's `/stats`
response and inspect authenticated site history plus one saved paper record.
Check net sample count, partial counts, funding-unknown count and displayed net R.
Do not send a bot command or notification to a user without explicit authorization;
an operator can invoke `/stats` to provide that verification.

Replay coverage is bounded by available candles: the current market-data adapter
fetches recent candles, and 15-minute exchange history is limited. Old or gapped
rows can remain unresolved even when replay completes without request errors.
Review the JSON coverage receipt; success is not proof that every historical row
was resolved. Funding outages are surfaced separately. Original source data and
prior correction evidence are preserved.
