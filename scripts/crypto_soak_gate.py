"""Fail-closed evidence checks shared by offline soak, packer and release preparation."""
from datetime import UTC, datetime, timedelta
import hashlib
import json
from pathlib import Path
import subprocess


def included(name):
    # Packing policy is local-only; the shipped validator needs only stdlib.
    from scripts.stage1_pack import included as legacy_included
    return (name.startswith('agent/') and not name.startswith('agent/tests/') and '__pycache__' not in name and (name.endswith('.py') or name in {'agent/Dockerfile','agent/requirements.txt','agent/.dockerignore'})) or (legacy_included(name) and name != 'docs/stage1_vps_baseline.json') or name in {
        'scripts/final_phase_admin.py', 'scripts/agent_vps_release.py', 'scripts/final_phase_audit.py', 'scripts/crypto_soak_gate.py'}


def fingerprint(hashes):
    return hashlib.sha256(json.dumps(hashes, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def source_identity(root):
    manifest = root / 'release-manifest.json'
    if manifest.exists():
        data = json.loads(manifest.read_text())
        sha, names = data['release_commit'], list(data['files'])
    else:
        sha = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()
        names = [name for name in subprocess.check_output(['git', '-C', str(root), 'ls-tree', '-r', '--name-only', sha], text=True).splitlines() if included(name)]
    return sha, fingerprint({name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in names})


def validate(report, commit, source_fingerprint, *, now=None):
    if not report.get('release_gate') or report.get('passed') is not True:
        raise ValueError('A passed release soak is required; diagnostics never qualify')
    if report.get('source_commit') != commit or report.get('source_fingerprint') != source_fingerprint:
        raise ValueError('Soak does not cover this exact candidate/source')
    if report.get('elapsed_seconds', 0) < 900 or report.get('requested_seconds', 0) < 900:
        raise ValueError('Release soak must cover at least 15 full minutes')
    completed = datetime.fromisoformat(report['completed_at'])
    age = (now or datetime.now(UTC)) - completed
    if not timedelta(0) <= age <= timedelta(hours=72):
        raise ValueError('Release soak must be recent (within 72 hours)')
    if report.get('archive') != 'on' or report.get('outcomes') != 'on':
        raise ValueError('Release gate requires both release features enabled')
    sizes, runtime = report.get('database_rows', {}), report.get('runtime', {})
    if sizes.get('trade_ideas', 0) < 40000 or sizes.get('signal_reviews', 0) < 1000000:
        raise ValueError('Production-sized database required')
    if runtime.get('scans_completed', 0) < 1 or runtime.get('lifecycle_completed', 0) < 1:
        raise ValueError('A full scan and lifecycle cycle must complete')
    if runtime.get('stored_markets', 0) < 45 or runtime.get('max_lifecycle_checked', 0) < 1:
        raise ValueError('Full crypto workload was not exercised')
    if report.get('new_score_evaluations', 0) < 1 or report.get('new_market_snapshots', 0) < 1:
        raise ValueError('Score archiving must execute, not merely be configured on')
    health = report.get('health', {})
    if health.get('successes', 0) < 850 or health.get('failures', 1) != 0 or health.get('worst_seconds', 99) > 2:
        raise ValueError('Health must stay responsive throughout the soak')
    if runtime.get('max_event_loop_lag_seconds', 99) > 2 or runtime.get('errors', 1):
        raise ValueError('Event-loop stalls or workload errors invalidate the soak')
    required = {'/api/top-ideas', '/api/active-crypto-trades', '/api/trade-history', '/api/trade-stats'}
    successful_endpoints = {p['endpoint'] for p in report.get('api_probes', []) if p.get('ok')}
    if not required <= successful_endpoints:
        raise ValueError('Every release API probe must be exercised successfully')
    if report.get('failures') or report.get('api_probe_failures'):
        raise ValueError('API/workload probes must all pass')
    return {'agent_load': report.get('agent_load'), 'passed': True, 'source_commit': commit, 'source_fingerprint': source_fingerprint,
            'elapsed_seconds': report['elapsed_seconds'], 'completed_at': report['completed_at'],
            'health_successes': health['successes'], 'worst_health_seconds': health['worst_seconds'],
            'scans_completed': runtime['scans_completed'], 'lifecycle_completed': runtime['lifecycle_completed']}


def validate_summary(summary, commit, hashes):
    if not summary or summary.get('passed') is not True or summary.get('source_commit') != commit or summary.get('source_fingerprint') != fingerprint(hashes):
        raise ValueError('Payload lacks matching passed soak evidence')
    if summary.get('elapsed_seconds', 0) < 900 or summary.get('health_successes', 0) < 850 or summary.get('worst_health_seconds', 99) > 2 or summary.get('scans_completed', 0) < 1 or summary.get('lifecycle_completed', 0) < 1:
        raise ValueError('Payload soak evidence is incomplete')
    age = datetime.now(UTC) - datetime.fromisoformat(summary['completed_at'])
    if not timedelta(0) <= age <= timedelta(hours=72):
        raise ValueError('Payload soak evidence expired')
