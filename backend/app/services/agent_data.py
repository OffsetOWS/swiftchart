"""Read-only projections of the same canonical services used by the website/bot."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from time import monotonic
from weakref import WeakKeyDictionary

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder

from app.config import SUPPORTED_TIMEFRAMES, get_settings
from app.forex.config import SUPPORTED_FOREX_PAIRS, normalize_forex_timeframe
from app.forex.models import DISPLAY_ACTIVE_FOREX_STATUSES
from app.forex.storage import list_signals, get_signal as get_forex_signal
from app.routes.forex import forex_pair_chart, forex_currency_strength, _enabled_signals
from app.services.crypto_work import run_crypto_work
from app.services.market_analysis import analyze_market_read_only, symbol_for_exchange
from app.services.market_data import get_candles_cached
from app.services.scanner import discover_all_scan_markets, read_cached_top_ideas
from app.services.trade_history import get_trade_history, list_active_crypto_trades
from app.models.schemas import RiskSettings


SIGNAL_FIELDS = {
    'id', 'signal_id', 'trade_id', 'symbol', 'exchange', 'timeframe', 'direction',
    'status', 'lifecycle_status', 'tracking_status', 'lifecycle_phase', 'entry_status', 'entry_type',
    'entry_price', 'entry_zone', 'entry_low', 'entry_high', 'entry_zone_low', 'entry_zone_high',
    'stop_loss', 'take_profit_1', 'take_profit_2', 'tp1_closes_position',
    'created_at', 'updated_at', 'closed_at', 'tracking_started_at', 'tracking_deadline',
    'entry_triggered_at', 'tp1_hit_at', 'tp2_hit_at', 'stop_hit_at',
    'decision', 'strategy_decision', 'setup_status', 'current_price', 'last_price',
}
ZONE_FIELDS = {'id', 'zone_id', 'type', 'side', 'role', 'lower', 'upper', 'low', 'high', 'state', 'created_at'}
_states = WeakKeyDictionary()


def signal_projection(value, market_type=None):
    value = jsonable_encoder(value)
    result = {key: val for key, val in value.items() if key in SIGNAL_FIELDS}
    if market_type:
        raw = str(value.get('lifecycle_status') or value.get('status') or value.get('entry_status') or value.get('strategy_decision') or 'UNAVAILABLE').upper()
        if market_type == 'crypto':
            # Same lifecycle-only presentation as frontend/src/lib/cryptoLifecycle.js.
            if raw == 'TP1_HIT':
                raw = 'TP1_HIT_TP2_RUNNING'
            elif raw in {'SL_HIT','PARTIAL_LOSS','BREAK_EVEN','CLOSED_AFTER_TP1'}:
                raw = 'STOPPED'
            elif raw in {'TAKEN','ACTIVE'}:
                raw = 'OPEN'
            elif raw in {'AMBIGUOUS','UNRESOLVED','EXPIRED_POSITION','EXPIRED_RUNNER'}:
                raw = 'UNRESOLVED'
        result['display_status'] = raw
    return result


def zone_projection(values):
    return [{key: val for key, val in item.items() if key in ZONE_FIELDS} for item in jsonable_encoder(values)]


def envelope(data, source):
    return {'schema_version': 'agent-v1', 'data': jsonable_encoder(data), 'source': source,
            'observed_at': datetime.now(UTC).isoformat()}


async def cached(key, produce):
    """Coalesce identical requests; bound work before admitting different keys."""
    state = _states.setdefault(asyncio.get_running_loop(), {'cache': {}, 'pending': {}})
    hit = state['cache'].get(key)
    if hit and monotonic() - hit[0] < 20:
        return hit[1]
    task = state['pending'].get(key)
    if task is None:
        if len(state['pending']) >= 2 and key != ('markets',):
            raise HTTPException(503, 'Agent read budget is busy; retry shortly')
        async def work():
            return await produce()
        task = asyncio.create_task(work())
        state['pending'][key] = task
        def complete(done):
            state['pending'].pop(key, None)
            if not done.cancelled() and done.exception() is None:
                if len(state['cache']) >= 128:
                    state['cache'].pop(next(iter(state['cache'])))
                state['cache'][key] = (monotonic(), done.result())
        task.add_done_callback(complete)
    return await asyncio.shield(task)


def timeframe_for(kind, value):
    if kind == 'forex':
        value = normalize_forex_timeframe(value)
        if value not in {'1H', '4H', '1D'}:
            raise HTTPException(422, 'Use 1H, 4H, or 1D for Forex charts')
    elif kind == 'crypto':
        value = value.lower()
        if value not in SUPPORTED_TIMEFRAMES:
            raise HTTPException(422, 'Unsupported crypto timeframe')
    else:
        raise HTTPException(422, 'Unsupported market type')
    return value


async def markets():
    rows = await discover_all_scan_markets('all')
    crypto = [{'symbol': row['symbol'], 'base_asset': row['symbol'].removesuffix('USDT'), 'exchange': row['exchange']} for row in rows]
    forex = [{'symbol': symbol, 'base_asset': symbol, 'exchange': 'forex'} for symbol in SUPPORTED_FOREX_PAIRS]
    return envelope({'crypto': crypto, 'forex': forex}, '/api/markets and canonical scanner eligibility')


async def require_symbol(kind, symbol):
    symbol = ''.join(c for c in symbol.upper() if c.isalnum())
    if kind == 'forex':
        if symbol not in SUPPORTED_FOREX_PAIRS:
            raise HTTPException(404, 'Unsupported Forex pair')
    elif kind == 'crypto':
        symbol = symbol_for_exchange(get_settings().default_exchange, symbol)
        available = (await cached(('markets',), markets))['data']['crypto']
        if symbol not in {row['symbol'] for row in available}:
            raise HTTPException(404, 'Unsupported crypto symbol')
    else:
        raise HTTPException(422, 'Unsupported market type')
    return symbol


async def scan(kind, timeframe, symbol=None):
    timeframe = timeframe_for(kind, timeframe)
    if symbol:
        symbol = await require_symbol(kind, symbol)
    if kind == 'forex':
        rows = await run_crypto_work(list_signals, DISPLAY_ACTIVE_FOREX_STATUSES, limit=500, timeframe=timeframe)
        rows = [signal_projection(row, market_type=kind) for row in rows]
        source = '/api/forex/signals'
        data = {'signals': rows}
    else:
        raw = read_cached_top_ideas(get_settings().default_exchange, timeframe)
        data = {'signals': [signal_projection(row, market_type='crypto') for row in raw.get('ideas', [])],
                'pending_setups': [signal_projection(row, market_type='crypto') for row in raw.get('pending_setups', [])],
                'updated_at': raw.get('updated_at'), 'available': bool(raw.get('updated_at') or raw.get('ideas') or raw.get('pending_setups'))}
        source = '/api/top-ideas scanner cache (no refresh requested)'
    if symbol:
        for key in ('signals', 'pending_setups'):
            if key in data:
                data[key] = [row for row in data[key] if row.get('symbol') == symbol]
    data.update(market_type=kind, timeframe=timeframe)
    return envelope(data, source)


async def signal(kind, identity):
    if kind == 'forex':
        row = await run_crypto_work(get_forex_signal, identity)
    elif kind == 'crypto' and identity.isdigit():
        row = await run_crypto_work(get_trade_history, int(identity))
    else:
        raise HTTPException(404, 'Signal not found')
    if row is None:
        raise HTTPException(404, 'Signal not found')
    return envelope(signal_projection(row, market_type=kind), '/api/forex/signals/{id}' if kind == 'forex' else '/api/trade-history/{id}')


async def active(kind='all', symbol=None):
    if kind not in {'all', 'crypto', 'forex'}:
        raise HTTPException(422, 'Unsupported market type')
    data = {}
    if kind in {'all', 'crypto'}:
        rows = await run_crypto_work(list_active_crypto_trades, exchange='all', limit=100, symbol=symbol)
        data['crypto'] = [signal_projection(row, market_type='crypto') for row in rows]
    if kind in {'all', 'forex'}:
        rows = await run_crypto_work(list_signals, DISPLAY_ACTIVE_FOREX_STATUSES, limit=500)
        data['forex'] = [signal_projection(row, market_type='forex') for row in _enabled_signals(rows)]
    if symbol:
        selected = ''.join(c for c in symbol.upper() if c.isalnum())
        data = {k: [r for r in rows if r.get('symbol') == selected] for k, rows in data.items()}
    return envelope(data, 'canonical active-trade projections used by the website and signal bot')


async def snapshot(kind, symbol, timeframe):
    symbol = await require_symbol(kind, symbol)
    timeframe = timeframe_for(kind, timeframe)
    if kind == 'forex':
        chart = jsonable_encoder(await forex_pair_chart(symbol=symbol, timeframe=timeframe, limit=240))
        data = {key: chart.get(key) for key in ('symbol', 'timeframe', 'bias', 'htf_context', 'regime', 'decision', 'current_price', 'levels', 'updated_at')}
        data['buy_zones'] = zone_projection(chart.get('buy_zones', []))
        data['sell_zones'] = zone_projection(chart.get('sell_zones', []))
        published = await active('forex', symbol)
        data['signals'] = [row for row in published['data']['forex'] if str(row.get('timeframe','')).upper() == timeframe.upper()]
        source = '/api/forex/pairs/{symbol}/chart + persisted Forex signals'
    else:
        settings = get_settings()
        analysis = jsonable_encoder(await analyze_market_read_only(
            exchange='all', symbol=symbol, timeframe=timeframe,
            risk=RiskSettings(account_size=settings.default_account_size,
                              risk_per_trade_pct=settings.default_risk_per_trade,
                              min_rr=settings.default_min_rr, max_open_trades=settings.default_max_open_trades,
                              preferred_timeframe=timeframe), supplemental_timeout_seconds=8,
        ))
        data = {key: analysis.get(key) for key in ('symbol', 'exchange', 'timeframe', 'current_price', 'market_condition', 'higher_timeframe_bias')}
        data['support_zones'] = zone_projection(analysis.get('support_zones', []))
        data['resistance_zones'] = zone_projection(analysis.get('resistance_zones', []))
        # Reanalysed candidate plans never become a claimed published signal.
        published = await active('crypto', symbol)
        data['signals'] = [row for row in published['data']['crypto'] if str(row.get('timeframe','')).lower() == timeframe.lower()]
        data['setup_status'] = 'PUBLISHED_SIGNAL' if data['signals'] else 'NO_PUBLISHED_SIGNAL'
        source = '/api/analyze read-only analysis + /api/active-crypto-trades canonical statuses'
    data['market_type'] = kind
    return envelope(data, source)


async def strength(horizon):
    raw = jsonable_encoder(await forex_currency_strength(horizon=horizon))
    raw.pop('available_pair_count', None)
    raw.pop('total_pair_count', None)
    return envelope(raw, '/api/forex/currency-strength')


async def volume(kind, symbol, timeframe):
    from app.services.volume_profile import calculate_volume_profile
    symbol = await require_symbol(kind, symbol)
    timeframe = timeframe_for(kind, timeframe)
    if kind == 'forex':
        from app.forex.market_data import get_forex_market_data_service
        pair = SUPPORTED_FOREX_PAIRS[symbol]
        frame = await get_forex_market_data_service().completed_candles(pair, timeframe, limit=240)
        tick = pair.pip_size / 10
    else:
        frame = await get_candles_cached(get_settings().default_exchange, symbol, timeframe, 240)
        tick = None
    result = await run_crypto_work(calculate_volume_profile, frame, rows=24, minimum_tick=tick)
    data = {key: result.get(key) for key in ('available', 'poc', 'vah', 'val', 'delta_percent')}
    data.update(symbol=symbol, market_type=kind, timeframe=timeframe)
    return envelope(data, 'SwiftChart V2 informational volume-profile calculation over API candle data')
