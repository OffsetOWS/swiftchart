"""Isolated offline API runner used by crypto_soak; never a production entry point."""
from __future__ import annotations

import asyncio
import cProfile
import faulthandler
import json
import logging
import os
from pathlib import Path
import re
import signal
import socket
import sqlite3
import sys
import threading
import time

ROOT = Path(os.environ['SOAK_CODE_ROOT']).resolve()
OUTPUT = Path(os.environ['SOAK_OUTPUT']).resolve()
sys.path[:0] = [str(ROOT / 'backend'), str(ROOT)]
sys.dont_write_bytecode = True
os.chdir(OUTPUT)
# No inherited application environment, live credentials or outbound sockets.
_original_connect = socket.socket.connect
_original_connect_ex = socket.socket.connect_ex


def local_only(address):
    if isinstance(address, tuple) and address[0] not in {'127.0.0.1', 'localhost', '::1'}:
        raise RuntimeError('Offline soak prohibits external connections')


def connect(self, address):
    local_only(address)
    return _original_connect(self, address)


def connect_ex(self, address):
    local_only(address)
    return _original_connect_ex(self, address)


socket.socket.connect = connect
socket.socket.connect_ex = connect_ex

# Also guard HTTP libraries when an optional native event loop bypasses Python's
# socket.connect method. Fixtures need no external HTTP requests.
import httpx
from urllib.parse import urlparse
_original_http_request = httpx.Client.request
_original_async_http_request = httpx.AsyncClient.request


def http_request(self, method, url, *args, **kwargs):
    local_only((urlparse(str(url)).hostname, 0))
    return _original_http_request(self, method, url, *args, **kwargs)


async def async_http_request(self, method, url, *args, **kwargs):
    local_only((urlparse(str(url)).hostname, 0))
    return await _original_async_http_request(self, method, url, *args, **kwargs)


httpx.Client.request = http_request
httpx.AsyncClient.request = async_http_request
METRICS = {'scans_completed': 0, 'lifecycle_completed': 0, 'errors': 0,
           'max_event_loop_lag_seconds': 0.0, 'queries': {}}
METRIC_LOCK = threading.Lock()


def note_query(sql, seconds):
    # Statements only: bound values, recipients and result rows are never logged.
    sql = re.sub(r"'[^']*'", '?', ' '.join(sql.split()))
    with METRIC_LOCK:
        q = METRICS['queries'].setdefault(sql, {'count': 0, 'seconds': 0.0, 'max_seconds': 0.0})
        q['count'] += 1
        q['seconds'] += seconds
        q['max_seconds'] = max(q['max_seconds'], seconds)


class MeasuredCursor(sqlite3.Cursor):
    def execute(self, sql, parameters=()):
        self.measured_sql = sql
        start = time.monotonic()
        try:
            return super().execute(sql, parameters)
        finally:
            note_query(sql, time.monotonic() - start)

    def fetchall(self):
        start = time.monotonic()
        try:
            return super().fetchall()
        finally:
            note_query(self.measured_sql + ' /* fetchall */', time.monotonic() - start)


class MeasuredConnection(sqlite3.Connection):
    def execute(self, sql, parameters=()):
        return self.cursor(MeasuredCursor).execute(sql, parameters)


_original_sqlite_connect = sqlite3.connect


def measured_connect(*args, **kwargs):
    kwargs.setdefault('factory', MeasuredConnection)
    return _original_sqlite_connect(*args, **kwargs)


sqlite3.connect = measured_connect
import pandas as pd


class StoredExchange:
    def __init__(self, candles):
        self.frames = {}
        self.requests = 0
        for path in sorted(candles.glob('*_4h.json')):
            data = json.loads(path.read_text())
            raw = data['candles']
            if not raw:
                continue
            records = []
            for bar in raw:
                if 't' in bar:
                    records.append({'timestamp': pd.to_datetime(bar['t'], unit='ms', utc=True),
                                    **{k: float(bar[v]) for k, v in [('open', 'o'), ('high', 'h'), ('low', 'l'), ('close', 'c'), ('volume', 'v')]},
                                    'complete': True})
                else:
                    records.append({**bar, 'timestamp': pd.to_datetime(bar['timestamp'], utc=True), 'complete': True})
            df = pd.DataFrame(records).sort_values('timestamp').drop_duplicates('timestamp')
            df.attrs.update(requested_at=data.get('requested_end'), fetched_at=data.get('fetched_at'), timeframe='4h', source_snapshot='stored-offline-candles')
            self.frames[data.get('symbol', path.stem.rsplit('_', 1)[0])] = df
        if len(self.frames) < 45:
            raise ValueError('Soak requires at least 45 stored crypto markets')

    async def get_markets(self):
        await asyncio.sleep(0.002)
        # Fixed equal liquid volumes exercise the full real rotating scan window.
        # These are load inputs, never historical liquidity/performance evidence.
        return [{'symbol': name, 'active': True, 'perpVolume24h': 1_000_000, 'volume': 1_000_000} for name in self.frames]

    async def get_candles(self, symbol, timeframe, limit):
        await asyncio.sleep(0.002)
        self.requests += 1
        df = self.frames[symbol]
        if timeframe != '4h':
            # Higher/intrabar fixtures must exist or fail explicitly. No fabricated prices.
            if timeframe == '1d':
                df = df.set_index('timestamp').resample('1D').agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'}).dropna().reset_index()
                df['complete'] = True
            else:
                raise ValueError('Stored fixture does not contain requested timeframe')
        result = df.tail(limit).copy()
        result.attrs.update(timeframe=timeframe, source_snapshot='stored-offline-candles')
        return result


exchange = StoredExchange(Path(os.environ['SOAK_CANDLES']))
from app.exchanges import factory
from app.services import market_data, scanner, crypto_lifecycle
from app.services import tracking_funding
from app import main
factory.get_exchange = lambda _: exchange
market_data.get_exchange = lambda _: exchange


class StoredForex:
    def __init__(self, path):
        snapshots = json.loads(Path(path).read_text())
        self.charts = {(row['symbol'], row['timeframe']): row for key in ('forex','forex_higher') for row in snapshots[key]}

    async def completed_candles(self, pair, timeframe, limit=240):
        row = self.charts[(pair.pair, timeframe)]
        bars = [bar for bar in row['candles'] if bar.get('complete', True)]
        frame = pd.DataFrame(bars)
        frame['timestamp'] = pd.to_datetime(frame['timestamp'], utc=True)
        return frame.tail(limit).copy()

    async def live_candle(self, pair, timeframe, **kwargs):
        rows = self.charts[(pair.pair, timeframe)]['candles']
        forming = [bar for bar in rows if not bar.get('complete', True)]
        if not forming:
            return None
        return {key: value for key, value in forming[-1].items() if key != 'complete'}


if os.environ.get('SOAK_AGENT_FOREX_FIXTURES'):
    from app.routes import forex as forex_routes
    stored_forex = StoredForex(os.environ['SOAK_AGENT_FOREX_FIXTURES'])
    forex_routes.get_forex_market_data_service = lambda: stored_forex


async def no_external_funding(*_args, **_kwargs):
    # Keep accounting code unchanged; unavailable fixture stays unknown.
    return None


async def no_forex_startup():
    return None


tracking_funding.fetch_rates = no_external_funding
main.verify_oanda_startup = no_forex_startup
main.start_forex_worker = lambda: None
# No bot, executor or subscription loop is started by this runner.


def monitor_job(function, kind):
    async def wrapped(*args, **kwargs):
        started = time.monotonic()
        try:
            result = await function(*args, **kwargs)
            with METRIC_LOCK:
                METRICS[kind + '_completed'] += 1
                METRICS[kind + '_last_seconds'] = time.monotonic() - started
                if kind == 'lifecycle':
                    METRICS['max_lifecycle_checked'] = max(METRICS.get('max_lifecycle_checked', 0), result.get('checked', 0))
                METRICS[kind + '_last_result'] = (result.get('scan_stats', {}) if kind == 'scans' else result)
            return result
        except Exception:
            with METRIC_LOCK:
                METRICS['errors'] += 1
            raise
    return wrapped


scanner.run_scan = monitor_job(scanner.run_scan, 'scans')
crypto_lifecycle.check_trade_outcomes = monitor_job(crypto_lifecycle.check_trade_outcomes, 'lifecycle')


async def heartbeat():
    previous = time.monotonic()
    last_write = 0
    while True:
        await asyncio.sleep(0.05)
        now = time.monotonic()
        with METRIC_LOCK:
            METRICS['max_event_loop_lag_seconds'] = max(METRICS['max_event_loop_lag_seconds'], now - previous - 0.05)
            if now - last_write >= 1:
                data = dict(METRICS)
                data['queries'] = sorted((dict(sql=k, **v) for k, v in METRICS['queries'].items()), key=lambda q: q['seconds'], reverse=True)[:12]
                data['stored_markets'] = len(exchange.frames)
                data['candle_requests'] = exchange.requests
                (OUTPUT / 'runtime.json').write_text(json.dumps(data, indent=2, default=str))
                last_write = now
        previous = now


@main.app.on_event('startup')
async def monitor_startup():
    # Candidate startup already migrated this LOCAL copy. Preserve an existing
    # production cutoff; activate a new schema locally only when it was absent.
    from app.utils.database import get_connection
    from datetime import datetime, UTC
    with get_connection() as c:
        if os.environ['SOAK_OUTCOMES'] == 'on':
            c.execute('UPDATE telegram_dispatch_config SET outcome_started_at=COALESCE(outcome_started_at,?) WHERE id=1', (datetime.now(UTC).isoformat(),))
        else:
            c.execute('UPDATE telegram_dispatch_config SET outcome_started_at=NULL WHERE id=1')
    asyncio.create_task(heartbeat())


if __name__ == '__main__':
    logging.basicConfig(level=logging.WARNING)
    prof = cProfile.Profile() if os.environ.get('SOAK_PROFILE') == 'true' else None
    def dump(*_):
        if prof:
            prof.dump_stats(OUTPUT / 'main-thread.pstats')
        faulthandler.dump_traceback()
    signal.signal(signal.SIGUSR1, dump)
    faulthandler.dump_traceback_later(30, repeat=True)
    if prof:
        prof.enable()
    import uvicorn
    try:
        uvicorn.run(main.app, host='127.0.0.1', port=int(os.environ['SOAK_PORT']), log_level='warning', access_log=False)
    finally:
        dump()
