"""Offline release gate: real API + copied DB + stored markets, 15-minute minimum."""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor
import resource
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import time
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.crypto_soak_gate import source_identity, validate


def run(args):
    source = args.database.resolve()
    identity = source_identity(args.code_root.resolve())
    if not args.diagnose:
        dirty = subprocess.check_output(['git', '-C', str(args.code_root), 'status', '--porcelain'], text=True)
        if dirty.strip():
            raise ValueError('Commit the candidate before the release soak; the source must remain clean')
    output = args.output.resolve()
    if not source.is_file() or output.exists():
        raise ValueError('Require an existing frozen database and a NEW private output directory')
    if not args.diagnose and args.seconds < 900:
        raise ValueError('Release soak must run for at least 900 seconds')
    if str(output).startswith(str(ROOT) + os.sep) and 'analysis_outputs' not in output.parts:
        raise ValueError('Keep private soak output outside source directories')
    output.mkdir(parents=True, mode=0o700)
    os.umask(0o077)
    database = output / 'runtime.private.db'
    shutil.copyfile(source, database)
    with sqlite3.connect(database) as c:
        sizes = {table: c.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0] for table in ('trade_ideas', 'signal_reviews', 'tracking_corrections')}
        if sizes['trade_ideas'] < args.min_trades or sizes['signal_reviews'] < args.min_reviews:
            raise ValueError('Database is below production-size release-gate thresholds')
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        initial_evaluations = c.execute('SELECT COUNT(*) FROM score_evaluations').fetchone()[0] if 'score_evaluations' in tables else 0
        initial_snapshots = c.execute('SELECT COUNT(*) FROM score_market_snapshots').fetchone()[0] if 'score_market_snapshots' in tables else 0
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0)); port = s.getsockname()[1]
    env = {k: os.environ[k] for k in ('PATH', 'LANG', 'LC_ALL', 'SYSTEMROOT') if k in os.environ}
    env.update(PYTHONDONTWRITEBYTECODE='1', DATABASE_URL='sqlite:///' + str(database), ENVIRONMENT='offline-soak',
               CRYPTO_SCORE_ARCHIVE_ENABLED=str(args.archive == 'on').lower(), FOREX_SCANNER_ENABLED='false',
               VARIATIONAL_ENABLED='false', GENLAYER_ENABLED='false', EXECUTION_AUTOTRADE_ENABLED='false',
               HYPERLIQUID_CANDLE_REQUEST_DELAY_SECONDS='0', API_RATE_LIMIT_PER_MINUTE='100000',
               SCANNER_RATE_LIMIT_PER_MINUTE='100000', SOAK_CODE_ROOT=str(args.code_root.resolve()), SOAK_OUTPUT=str(output), SOAK_CANDLES=str(args.candles.resolve()),
               SOAK_PROFILE=str(args.profile).lower(), SOAK_PORT=str(port), SOAK_OUTCOMES=args.outcomes)
    logs = open(output / 'runtime.private.log', 'w')
    cpu_before = resource.getrusage(resource.RUSAGE_CHILDREN)
    process = subprocess.Popen([sys.executable, str(ROOT / 'scripts/crypto_soak_runtime.py')], cwd=output, env=env, stdout=logs, stderr=logs)
    observed = []
    failures = []
    started = None
    elapsed = 0
    api_probes = []
    probe_pool = ThreadPoolExecutor(max_workers=3)
    pending_probes = []
    def api_probe(path, timeout):
        t = time.monotonic()
        try:
            with urlopen(f'http://127.0.0.1:{port}' + path, timeout=timeout) as response:
                assert response.status == 200
                # Consume but never retain or print private response contents.
                json.load(response)
            return {'endpoint': path.split('?')[0], 'ok': True, 'seconds': time.monotonic()-t}
        except Exception as exc:
            return {'endpoint': path.split('?')[0], 'ok': False, 'reason': type(exc).__name__, 'seconds': time.monotonic()-t}
    try:
        ready_deadline = time.monotonic() + 90
        while time.monotonic() < ready_deadline:
            if process.poll() is not None:
                raise RuntimeError('Isolated API exited before readiness; inspect private log')
            try:
                with urlopen(f'http://127.0.0.1:{port}/health', timeout=1) as r:
                    if r.status == 200:
                        started = time.monotonic(); break
            except Exception:
                time.sleep(0.2)
        if started is None:
            raise RuntimeError('Isolated API did not become ready')
        print(json.dumps({'ready': True, 'archive': args.archive, 'outcomes': args.outcomes, 'database_rows': sizes}), flush=True)
        next_probe = started
        next_api_probe = started
        next_stats_probe = started + 15
        while time.monotonic() - started < args.seconds:
            if process.poll() is not None:
                failures.append({'reason': 'api-exited'}); break
            t = time.monotonic()
            try:
                with urlopen(f'http://127.0.0.1:{port}/health', timeout=args.max_latency) as r:
                    assert r.status == 200
                elapsed = time.monotonic() - t
                observed.append(elapsed)
                if elapsed > args.max_latency:
                    failures.append({'reason': 'health-slow', 'at_seconds': t-started})
            except Exception as e:
                failures.append({'reason': type(e).__name__, 'at_seconds': t-started})
            if not args.diagnose and t >= next_api_probe:
                for path in ['/api/top-ideas?exchange=hyperliquid&timeframe=4h', '/api/active-crypto-trades', '/api/trade-history?limit=20']:
                    pending_probes.append(probe_pool.submit(api_probe, path, args.max_latency))
                next_api_probe = t + 10
            if not args.diagnose and t >= next_stats_probe:
                pending_probes.append(probe_pool.submit(api_probe, '/api/trade-stats', 30))
                next_stats_probe = t + 120
            for job in list(pending_probes):
                if job.done():
                    api_probes.append(job.result()); pending_probes.remove(job)
            next_probe += args.probe_interval
            time.sleep(max(0, next_probe-time.monotonic()))
        elapsed = time.monotonic() - started
        for job in pending_probes:
            api_probes.append(job.result())
        process.send_signal(signal.SIGUSR1)
        time.sleep(0.1)
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill(); process.wait()
        logs.close()
        probe_pool.shutdown(wait=True, cancel_futures=True)
    runtime = json.loads((output/'runtime.json').read_text()) if (output/'runtime.json').exists() else {}
    if runtime.get('scans_completed', 0) < 1 or runtime.get('lifecycle_completed', 0) < 1:
        failures.append({'reason': 'full-scan-and-lifecycle-not-completed'})
    if runtime.get('errors') or runtime.get('lifecycle_last_result', {}).get('errors'):
        failures.append({'reason': 'workload-errors'})
    if runtime.get('max_event_loop_lag_seconds', 0) > args.max_latency:
        failures.append({'reason': 'event-loop-lag'})
    if source_identity(args.code_root.resolve()) != identity:
        failures.append({'reason': 'source-changed-during-soak'})
    if not args.diagnose and subprocess.check_output(['git', '-C', str(args.code_root), 'status', '--porcelain'], text=True).strip():
        failures.append({'reason': 'source-dirty-after-soak'})
    with sqlite3.connect(database) as c:
        new_evaluations = c.execute('SELECT COUNT(*) FROM score_evaluations').fetchone()[0] - initial_evaluations
        new_snapshots = c.execute('SELECT COUNT(*) FROM score_market_snapshots').fetchone()[0] - initial_snapshots
    cpu_after = resource.getrusage(resource.RUSAGE_CHILDREN)
    cpu_seconds = cpu_after.ru_utime + cpu_after.ru_stime - cpu_before.ru_utime - cpu_before.ru_stime
    api_failures = [row for row in api_probes if not row['ok']]
    sha = identity[0]
    report = {'completed_at': datetime.now(UTC).isoformat(), 'source_commit': sha,
              'release_gate': not args.diagnose, 'requested_seconds': args.seconds, 'elapsed_seconds': elapsed, 'source_fingerprint': identity[1], 'cpu_seconds': cpu_seconds,
              'archive': args.archive, 'outcomes': args.outcomes, 'database_rows': sizes,
              'new_score_evaluations': new_evaluations, 'new_market_snapshots': new_snapshots, 'api_probes': api_probes, 'api_probe_failures': api_failures,
              'health': {'successes': len(observed), 'failures': sum(f['reason'] not in {'full-scan-and-lifecycle-not-completed', 'workload-errors', 'event-loop-lag', 'source-changed-during-soak', 'source-dirty-after-soak'} for f in failures), 'worst_seconds': max(observed, default=None),
                         'p95_seconds': sorted(observed)[int((len(observed)-1)*.95)] if observed else None},
              'passed': not failures and not api_failures, 'failures': failures, 'runtime': runtime}
    if not args.diagnose:
        try:
            validate(report, identity[0], identity[1])
        except (ValueError, KeyError) as exc:
            report['passed'] = False
            report['failures'].append({'reason': str(exc)})
    (output/'report.json').write_text(json.dumps(report, indent=2, default=str))
    print(json.dumps({k: v for k, v in report.items() if k not in {'runtime', 'api_probes', 'api_probe_failures', 'failures'}}, indent=2), flush=True)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--database', type=Path, required=True)
    p.add_argument('--candles', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--code-root', type=Path, default=ROOT)
    p.add_argument('--seconds', type=float, default=900)
    p.add_argument('--diagnose', action='store_true', help='Short diagnostic only; never qualifies as a release gate')
    p.add_argument('--archive', choices=['on','off'], default='on')
    p.add_argument('--outcomes', choices=['on','off'], default='on')
    p.add_argument('--profile', action='store_true')
    p.add_argument('--max-latency', type=float, default=2)
    p.add_argument('--probe-interval', type=float, default=1)
    p.add_argument('--min-trades', type=int, default=40000)
    p.add_argument('--min-reviews', type=int, default=1000000)
    raise SystemExit(run(p.parse_args()))
