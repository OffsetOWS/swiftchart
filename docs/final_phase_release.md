# Final phase: crypto lifecycle messages and private score evidence

This is a candidate based on deployed Stage 2 commit `f55c8dc29f4240ed21432fede75b98d4af60a23f` on PR #11. Run the following commands only after the owner gives one explicit yes covering backend and frontend. Do not merge the PR.

## Reviewed behavior and limits

- Crypto alert levels and the exact UTC tracking deadline are factual. The unchanged audited tracker starts at the first full tracking candle after creation; its window then lasts 48 hours. For a 4h signal, that deadline is 48–52h after creation, not 48h after Telegram delivery. The deadline is shared by the alert, active-feed detail and tracker. All supported crypto timeframes use a 48h window.
- Outcome children use the canonical outbox, committed tracker fills and original successful recipient receipts. TP1/TP2/stop/time-exit replies have one logical event/recipient row. Ambiguous/unresolved outcomes wait for settlement. Old events before activation are excluded. Original recipients delivered after an event are excluded too.
- The same bot poll drains initial and outcome work. A confirmed Telegram response records its message ID and whitelisted reply ID. Rate-limit rejection can retry; an uncertain network send or stale claim is held rather than automatically resent. Telegram has no sendMessage idempotency key, so exactly-once external delivery cannot be guaranteed after an unknown HTTP result. Inspect these holds; never blindly reset them.
- Published/attempted fill facts and original alerted levels are lifecycle locks. Competing replay evidence is preserved privately for review, with further outcome messages held. It never silently overwrites the accepted projection behind a sent reply.
- Internal qualification remains 65 with unchanged weights and decision rules. Caps keep highest reported 24h volume; existing 3-per-scan, 2-per-direction and volume-floor settings remain. Public score/grade and strategy performance displays are removed. Forex strategy, data, tracking and dispatch code remain unchanged; shared presentation hides the score.
- Score capture is additive and off by default. This approved rollout explicitly enables it. New evaluations preserve raw component inputs, pre-rounding R:R plus the actually used rounded R:R, fallback branches, ordered adjustments, final score/grade, exact source/dependency/library versions, source candles/raw price strings, fetch/knowledge/completion times, HTF/global/breadth context and market volume. Initial evidence is immutable; rescans/retests append linked observations. New admitted opportunities, their canonical parent outbox and evidence persist atomically. A failed archive does not publish an unarchived opportunity. No guessed historical components are backfilled.
- Frontend publication uploads only frontend source and its Vercel configuration. The API is still the VPS. The backend destination is private deployment configuration rather than a committed IP. Git-triggered publication is disabled even when build-only settings are unavailable; a production build separately rejects a missing backend origin before compiling assets. The manual release path is deliberate. [Vercel configuration](https://vercel.com/docs/project-configuration/vercel-ts), [Git publication control](https://vercel.com/docs/project-configuration/git-configuration).
- No fee/funding or Forex strategy changes, dependency installation, second delivery/scan loop, historical maintenance replay or strategy performance claims are included. The separate 60-day study uses frozen Stage 2 inputs locally.

## Rollback, prepared before starting

Backend rollback stops only API/bot, archives all post-deploy writes into `before-rollback.db`, restores the integrity-checked `before.db`, and recreates the saved previous PM2 definitions at `f55c8dc29f4240ed21432fede75b98d4af60a23f`. The executor, original dirty checkout, shared environment, virtualenvs and bot-state files are not reset or replaced. Archives are never overwritten. Telegram messages cannot be unsent; preserve and reconcile receipts from the archive before any later retry.

Frontend rollback returns the existing project and canonical aliases to the exact deployment saved by local `prepare`. If Vercel instant rollback is unavailable because of the plan’s rollback-depth limit, first verify the aliases still point to this attempted release, then restore `swiftchart.xyz` and `swiftchart.vercel.app` with `vercel alias set "$saved_previous_url" "$alias" --scope offsetows-projects`. Verify both targets afterward. This uses the existing saved build; never create a replacement build or change plans during recovery. The prepared target at review was `dpl_56jDR3HyynxEUbY17UoBRCdFh7AQ`; prepare/deploy refuse unexpected alias drift. A staged/published release is never silently deployed a second time.

```bash
# Local: restore frontend only if this attempted release changed/staged it.
python3 scripts/final_phase_frontend.py rollback --approved \
  --cli "$vercel_cli" --state "$frontend_state"

# VPS: restore database + previous commit, restart API/bot, verify health.
ssh swiftchart-vps bash -s -- "$release_sha" <<'ROLLBACK'
set -euo pipefail
umask 077
release_sha="$1"
root="/opt/swiftchart-releases/$release_sha"
state="/opt/swiftchart-final-state/$release_sha"
/opt/swiftchart/backend/.venv/bin/python "$root/scripts/final_phase_admin.py" \
  rollback --root "$root" --state "$state"
curl --fail --silent --retry 60 --retry-delay 1 --retry-connrefused http://localhost:8000/health
pm2 save
ROLLBACK
```

## Local preparation — no deploy

The [mandatory 15-minute offline soak](crypto_release_gate.md) is required for every future release. Run against the exact committed candidate and private production-sized database copy. Unit tests and startup health cannot replace it.

Use the immutable candidate SHA in the private approval report. The evidence directory and state are private and excluded from Git. Keep the existing frontend production alias and backend source verified before approval.

```bash
python3 scripts/check_stage1.py
(cd frontend && npm test && SWIFTCHART_RELEASE_SHA="$release_sha" npm run build)
python3 scripts/crypto_soak.py --database "$private_frozen_db" \
  --candles "$private_candles_dir" --output "$new_private_soak_directory"
python3 -m scripts.final_phase_pack --ref "$release_sha" --output "$payload" \
  --soak-report "$new_private_soak_directory/report.json"
python3 scripts/final_phase_frontend.py prepare --commit "$release_sha" \
  --cli "$vercel_cli" --state "$frontend_state" \
  --link "/Users/doflam/Documents/New project/.vercel/project.json"
```

Scan the candidate commit range with Gitleaks redaction enabled, the complete extracted backend payload, the exact frontend dry-run upload set, and the compiled frontend. Check public IPs separately. Check the audited rules SHA and every Forex source hash against the base. No production evidence, env files, credentials or recipient IDs belong in the commit or frontend upload.

## Backend — only after yes

Set `release_sha`, `payload`, `frontend_state` and `vercel_cli` to the exact reviewed values. Copy a commit-built payload; never sync a working directory or reset `/opt/swiftchart`.

```bash
scp "$payload" "swiftchart-vps:/tmp/swiftchart-final-$release_sha.tar.gz"
ssh swiftchart-vps bash -s -- "$release_sha" <<'BACKEND'
set -euo pipefail
umask 077
release_sha="$1"
root="/opt/swiftchart-releases/$release_sha"
state="/opt/swiftchart-final-state/$release_sha"
python=/opt/swiftchart/backend/.venv/bin/python
previous=f55c8dc29f4240ed21432fede75b98d4af60a23f
[ ! -e "$root" ] && [ ! -e "$state" ]
mkdir -p "$root"
tar -xzf "/tmp/swiftchart-final-$release_sha.tar.gz" -C "$root"
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH="$root/backend:$root"
(cd "$root" && "$python" -c 'from app.main import app')
(cd "$root" && /opt/swiftchart/bot/.venv/bin/python -c 'from bot.main import main')
admin="$root/scripts/final_phase_admin.py"
"$python" "$admin" prepare --root "$root" --state "$state" \
  --previous-root "/opt/swiftchart-releases/$previous" \
  --previous-state "/opt/swiftchart-stage2-state/$previous" \
  --expected-commit "$release_sha"
backed_up=false
stopped=false
recover() {
  status=$?
  trap - ERR
  if [ "$backed_up" = true ]; then
    "$python" "$admin" rollback --root "$root" --state "$state" > "$state/rollback.log" 2>&1
    pm2 save > "$state/rollback-pm2-save.log"
  elif [ "$stopped" = true ]; then
    pm2 restart swiftchart-api swiftchart-bot --update-env > "$state/backup-recovery.log"
  fi
  exit "$status"
}
trap recover ERR
pm2 stop swiftchart-api swiftchart-bot > "$state/pm2-stop.log"
stopped=true
"$python" "$admin" backup --root "$root" --state "$state"
backed_up=true
"$python" "$admin" schema --root "$root" --state "$state" > "$state/schema.json"
# Restart immediately after the additive schema/cutoff step.
"$python" "$admin" activate --root "$root" --state "$state" > "$state/activate.log"
curl --fail --silent --retry 60 --retry-delay 1 --retry-connrefused \
  --max-time 5 http://localhost:8000/health > "$state/health.json"
"$python" "$admin" verify --root "$root" --state "$state"
curl --fail --silent --max-time 120 http://localhost:8000/api/trade-stats \
  > "$state/internal-trade-stats.json"
sleep 5
"$python" "$admin" verify --root "$root" --state "$state"
pm2 save > "$state/pm2-save.log"
trap - ERR
BACKEND
```

If source, PM2 paths, backup, schema or health checks fail, stop and report. Preserve all release/state directories and error logs. Do not attempt a second deployment automatically.

## Frontend — backend must already be healthy

The helper checks actual API/bot process directories and backend health immediately before creating a production build, then again before promotion. It builds the exact Git commit in a temporary workspace containing only the frontend. It stages one production deployment without moving domains, promotes that ready build, moves `swiftchart.xyz`, and verifies `release.json` equals the same SHA. Production public environment settings stay on the existing Vercel project. Its two Supabase values are protected and cannot be pulled locally. A build-time guard validates the actual injected values before compiling: only publishable/anon keys are permitted, credential/IP URLs are rejected, and the production API is explicitly same-origin. No protected values are copied locally. It sends no private analysis or backend source.

```bash
python3 scripts/final_phase_frontend.py deploy --approved \
  --cli "$vercel_cli" --state "$frontend_state"
# Verify the public proxy after the custom domain moves.
curl --fail --silent --max-time 30 https://swiftchart.xyz/health
# Push the exact tested/deployed SHA to existing PR #11. Never force-push.
git push origin "${release_sha}:refs/heads/codex/stage1-vps-tracking"
```

If frontend build/promotion/verification fails, inspect its private state first. Roll back this release’s frontend and backend using the commands above, then stop and report. Never replace an alias changed by another release; the helper refuses that drift. If Git push fails after successful publication, report the deployed SHA and keep the healthy deployment while resolving the push without rewriting someone else’s changes.

## First live evidence, then stop

Run this read-only command after activation and again on first actual deliveries/outcomes. Save owner-only output locally; do not post metrics or recipient evidence to the PR or frontend.

```bash
ssh swiftchart-vps bash -s -- "$release_sha" <<'OBSERVE'
set -euo pipefail
root="/opt/swiftchart-releases/$1"
/opt/swiftchart/backend/.venv/bin/python "$root/scripts/final_phase_audit.py" \
  --database /opt/swiftchart/swiftchart.db
OBSERVE
```

Report creation-to-first-delivery and all-recipient median/worst delay, pending/failure statuses, per-event successful receipts, original reply-ID checks, agreement with the current tracker, uniqueness, uncertain delivery holds and archive coverage. Check the public frontend release SHA and browser privacy. Unseen TP1/TP2/stop/time-exit cases remain explicitly pending; tests are not live observations, and a single trade cannot both reach its final target and later stop. A 48h exit cannot be certified early. Review unresolved replay conflicts privately; do not resolve them silently.

Finally recheck PR #11’s current head, mergeability and required checks. Tell the owner exactly when it is ready; never merge it on their behalf.
