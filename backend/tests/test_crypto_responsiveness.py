"""Regress the real metadata-copy stall and the mandatory deployment soak gate."""
import asyncio
from contextvars import ContextVar
from datetime import UTC, datetime
import json
import threading
import time

import pytest
from app.config import get_settings
from app.models.schemas import RiskSettings
from app.services import scanner, score_archive
from app.services.crypto_work import run_crypto_work
from scripts import crypto_soak_gate as gate, final_phase_admin as admin
from test_market_regime import candles_from_prices


class NoDeepcopy(dict):
    def __deepcopy__(self, memo):
        raise AssertionError('Cross-market evidence was copied by pandas')


def test_actual_scan_analysis_keeps_cross_market_evidence_out_of_pandas(monkeypatch):
    monkeypatch.setenv('CRYPTO_SCORE_ARCHIVE_ENABLED', 'true')
    get_settings.cache_clear()
    df = candles_from_prices([100 + (n % 8) * .1 for n in range(260)])
    candidate = scanner.Candidate('hyperliquid', 'BTCUSDT', 'BTCUSDT', df, 1, 1)
    snapshots = [NoDeepcopy(exchange='hyperliquid', symbol=f'COIN{n}', rows=[{'close': 100}]) for n in range(45)]
    observed = []
    original = scanner.analyze_dataframe
    def analyze(*args, **kwargs):
        # These real pandas operations triggered the production regression.
        args[3].tail(80)['close'].rolling(14).mean()
        response = original(*args, **kwargs)
        with score_archive.capture_context(args[0], args[1], args[2], args[3], args[4], args[5], -12, 42):
            observed.extend(score_archive.CONTEXT.get()['market_snapshots'])
        return response
    monkeypatch.setattr(scanner, 'analyze_dataframe', analyze)
    monkeypatch.setattr(scanner, 'save_signal_reviews', lambda _: None)
    try:
        scanner._analyze_with_scan_context(candidate, '4h', RiskSettings(), [], -12, 42,
                                          {'perpVolume24h': 500000}, snapshots)
        assert {s['symbol'] for s in observed} >= {f'COIN{n}' for n in range(45)}
        assert score_archive.EXTERNAL_CONTEXT.get() is None
    finally:
        get_settings.cache_clear()


def test_worker_preserves_context_and_api_loop_while_work_is_busy():
    marker = ContextVar('worker-test-marker', default=None)
    thread_ids, active = set(), []
    lock = threading.Lock()
    async def scenario():
        marker.set('evidence-context')
        pulses = 0
        def busy():
            assert marker.get() == 'evidence-context'
            assert threading.current_thread() is not threading.main_thread()
            with lock:
                thread_ids.add(threading.get_ident()); active.append(1)
                assert len(active) <= 2
            try:
                # A busy phase must not occupy the API's asyncio thread.
                time.sleep(.08)
            finally:
                with lock:active.pop()
        tasks = [asyncio.create_task(run_crypto_work(busy)) for _ in range(8)]
        while not all(t.done() for t in tasks):
            await asyncio.sleep(.005); pulses += 1
        await asyncio.gather(*tasks)
        assert pulses >= 10
    asyncio.run(scenario())
    assert len(thread_ids) == 2


def receipt():
    return {'release_gate': True, 'passed': True, 'source_commit': 'candidate',
            'source_fingerprint': 'fingerprint', 'elapsed_seconds': 901, 'requested_seconds': 900,
            'completed_at': datetime.now(UTC).isoformat(), 'archive': 'on', 'outcomes': 'on',
            'database_rows': {'trade_ideas': 40526, 'signal_reviews': 1648802},
            'new_score_evaluations': 1, 'new_market_snapshots': 1,
            'health': {'successes': 900, 'failures': 0, 'worst_seconds': .2},
            'runtime': {'scans_completed': 2, 'lifecycle_completed': 1, 'stored_markets': 45,
                        'max_lifecycle_checked': 652, 'max_event_loop_lag_seconds': .1, 'errors': 0},
            'failures': [], 'api_probe_failures': [], 'api_probes': [{'endpoint': p, 'ok': True} for p in ['/api/top-ideas', '/api/active-crypto-trades', '/api/trade-history', '/api/trade-stats']]}


@pytest.mark.parametrize('change', [
    {'release_gate': False}, {'elapsed_seconds': 899}, {'archive': 'off'},
    {'source_commit': 'different'}, {'source_fingerprint': 'changed-code'},
    {'new_score_evaluations': 0}, {'api_probe_failures': [{'ok': False}]},
    {'health': {'successes': 899, 'failures': 1, 'worst_seconds': .1}},
    {'runtime': {'scans_completed': 0, 'lifecycle_completed': 1}},
])
def test_short_diagnostic_stale_or_incomplete_soaks_cannot_authorize_release(change):
    report = {**receipt(), **change}
    with pytest.raises(ValueError):gate.validate(report, 'candidate', 'fingerprint')


def test_completed_soak_is_bound_to_candidate_and_runtime_sources(tmp_path):
    report = receipt()
    summary = gate.validate(report, 'candidate', 'fingerprint')
    assert summary['passed']
    with pytest.raises(ValueError):gate.validate_summary(summary, 'candidate', {'changed.py': 'hash'})
    manifest = tmp_path/'release-manifest.json'
    manifest.write_text(json.dumps({'files': {}}))
    with pytest.raises(ValueError, match='soak'):admin.require_soak(tmp_path, 'candidate')


@pytest.mark.parametrize('detail', [False, True])
def test_history_projection_and_evidence_share_snapshot_during_worker_commit(monkeypatch, tmp_path, detail):
    from app.services import trade_history as history
    from app.services.tracking_ledger import record_correction
    from app.utils import database
    from test_canonical_telegram_dispatch import configure_db, idea
    configure_db(monkeypatch, tmp_path)
    identifier = history.save_trade_ideas([idea()])[0]
    with database.get_connection() as c:
        c.execute('PRAGMA journal_mode=WAL')
        c.execute('UPDATE trade_ideas SET pnl_r_multiple=1 WHERE id=?', (identifier,))
        record_correction(c, 'CRYPTO', identifier, {}, {'pnl_r_multiple': 1},
                          {'category': 'resolved', 'booked_r': 1}, datetime.now(UTC).isoformat())
    original = history._public_tracking_rows
    def commit_between_projection_and_evidence(connection, rows):
        with database.get_connection() as writer:
            writer.execute('UPDATE trade_ideas SET pnl_r_multiple=2 WHERE id=?', (identifier,))
            record_correction(writer, 'CRYPTO', identifier, {}, {'pnl_r_multiple': 2},
                              {'category': 'resolved', 'booked_r': 2}, datetime.now(UTC).isoformat())
        return original(connection, rows)
    monkeypatch.setattr(history, '_public_tracking_rows', commit_between_projection_and_evidence)
    row = history.get_trade_history(identifier) if detail else history.query_trade_history({})['records'][0]
    assert row['gross_r_multiple'] == row['tracking_evidence']['booked_r'] == 1
    with database.get_connection() as c:
        assert c.execute('SELECT pnl_r_multiple FROM trade_ideas WHERE id=?', (identifier,)).fetchone()[0] == 2


def test_stats_releases_reader_before_enrichment_and_keeps_snapshot(monkeypatch, tmp_path):
    from app.services import trade_history as history
    from app.services.tracking_ledger import record_correction
    from app.utils import database
    from test_canonical_telegram_dispatch import configure_db, idea
    configure_db(monkeypatch, tmp_path)
    identifier = history.save_trade_ideas([idea()])[0]
    at = datetime.now(UTC).isoformat()
    def evidence(value):
        return {'category': 'resolved', 'booked_r': value, 'validated_entry_at': at, 'remaining_quantity': 0}
    with database.get_connection() as c:
        assert c.execute('PRAGMA journal_mode').fetchone()[0] == 'delete'
        c.execute("UPDATE trade_ideas SET pnl_r_multiple=1,closed_at=?,status='TP2_HIT',result='WIN',strategy_decision='TRADE',entry_status='READY',outcome_tracking_mode='PRODUCTION',strategy_version=NULL,executable_at=? WHERE id=?", (at, at, identifier))
        record_correction(c, 'CRYPTO', identifier, {}, {'pnl_r_multiple': 1}, evidence(1), at)
        plan = c.execute("EXPLAIN QUERY PLAN SELECT CASE WHEN COALESCE(accepted,0) != 0 THEN 'accepted' ELSE 'rejected' END AS status,COUNT(*),AVG(COALESCE(base_score,0)),AVG(COALESCE(adjusted_score,0)) FROM signal_reviews GROUP BY 1").fetchall()
        assert any('idx_signal_reviews_statistics' in row[3] for row in plan)
    original = history._public_tracking_rows
    def enrich_after_writer(connection, rows, *, evidence_json=None):
        # With rollback journaling this commit would time out if stats still
        # held its original read transaction during the slow enrichment phase.
        with database.get_connection() as writer:
            writer.execute('UPDATE trade_ideas SET pnl_r_multiple=2 WHERE id=?', (identifier,))
            record_correction(writer, 'CRYPTO', identifier, {}, {'pnl_r_multiple': 2}, evidence(2), at)
        assert connection is None
        assert json.loads(evidence_json[identifier])['booked_r'] == 1
        return original(connection, rows, evidence_json=evidence_json)
    monkeypatch.setattr(history, '_public_tracking_rows', enrich_after_writer)
    result = history.stats()
    assert result['gross_r_total'] == 1
