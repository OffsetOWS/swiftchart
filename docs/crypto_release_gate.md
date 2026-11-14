# Mandatory offline crypto release soak

Before every deployment, commit the candidate, run the full tests and scan the source/payload for secrets and server addresses. Then run the real API against a **new local copy** of the frozen production database, with stored candles and no outbound connections, for at least **900 seconds after readiness**. The source must stay clean and unchanged throughout. No production process, database or subscriber is used by the runner.

```bash
python scripts/check_stage1.py
python scripts/crypto_soak.py \
  --database "$private_frozen_db" --candles "$private_candles_dir" \
  --output "$new_private_soak_directory"
python -m scripts.final_phase_pack --ref "$release_sha" --output "$payload" \
  --soak-report "$new_private_soak_directory/report.json"
```

The gate requires at least 40,000 trade records, 1,000,000 reviews, 45 stored markets, a completed real scan and lifecycle cycle, and actual new score evaluations and candle snapshots. Both archiving and outcome events must be enabled. Health is probed every second; no probe may fail or exceed two seconds. At least 850 successful probes must span the full 15 minutes. Cache/active-trade/history routes are also probed, and the internal statistics API must return successfully while health remains responsive. Event-loop lag must remain below two seconds and workloads must not fail. The report is valid for 72 hours and only for that exact commit and runtime source fingerprint. The packer refuses missing/failed/short/mismatched evidence; backend preparation independently checks the receipt embedded in the payload manifest.

`--diagnose --seconds 90 --archive off --outcomes on` is for feature isolation only and **never** qualifies for release. Diagnostics with a short time budget can finish before a complete lifecycle cycle: report responsiveness separately from workload completion.

The harness clears the application environment, starts no bot or executor, disables Forex startup in the harness without editing Forex source, and blocks external socket connections. It keeps the real crypto scanner, tracker, score archive, SQLite population and HTTP server. Stored 4h candles drive the normal rotating scan window; daily fixture bars are aggregated from those real candles. Fixture market volumes are fixed load inputs, not historical liquidity evidence. Missing intrabar/funding data stays unavailable/unknown; no prices or costs are invented. The report cannot establish live exchange availability, VPS-specific capacity, Telegram delivery or production sign-in. Raw database/profile/log artifacts stay private, outside the repository.

After a passed gate and explicit deployment approval, use the normal backup → schema → immediate API/bot restart procedure. Confirm source directories, internal API and sustained health through active worker startup before publishing the frontend. Roll back on failure and preserve post-deploy writes; do not automatically try another deployment. This local soak complements live health checks.
