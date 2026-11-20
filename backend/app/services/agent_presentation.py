"""API-owned display facts. The agent never rounds or derives market numbers."""
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


def decimal(value):
    try:
        result = Decimal(str(value))
        return result if result.is_finite() and result > 0 else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def price_text(value, kind, symbol, precision):
    number = decimal(value)
    if number is None:
        return None
    if kind == 'forex':
        places = 2 if symbol == 'XAUUSD' else 3 if symbol.endswith('JPY') else 5
        tick = Decimal(1).scaleb(-places)
    elif precision.get('kind') == 'hyperliquid-perp' and isinstance(precision.get('size_decimals'), int):
        # Hyperliquid perp prices: five significant figures, at most
        # 6-szDecimals decimal places; integer prices are always valid.
        places = max(0, min(6 - precision['size_decimals'], 4 - number.adjusted()))
        tick = Decimal(1).scaleb(-places)
    else:
        tick = decimal(precision.get('tick_size'))
        if tick is None:
            return None  # Unknown precision must never become an invented tick.
        places = max(0, -tick.normalize().as_tuple().exponent)
    rounded = (number / tick).quantize(Decimal(1), rounding=ROUND_HALF_UP) * tick
    return f'{rounded:.{places}f}'


def nearest_zones(data):
    """Completed Forex roles stay untouched; omit wrong-side/terminal bands.

    In particular a forming Forex quote is NOT a confirmed role reversal.
    """
    price = decimal(data.get('current_price'))
    supports, resistances = [], []
    if price is None:
        return None, None
    for key, destination in [('support_zones', supports), ('buy_zones', supports),
                             ('resistance_zones', resistances), ('sell_zones', resistances)]:
        for zone in data.get(key, []):
            if str(zone.get('state', '')).upper() in {'BROKEN', 'INVALIDATED', 'EXPIRED'}:
                continue
            low, high = decimal(zone.get('lower', zone.get('low'))), decimal(zone.get('upper', zone.get('high')))
            if low is None or high is None or low > high:
                continue
            if (destination is supports and low <= price) or (destination is resistances and high >= price):
                destination.append((low, high))
    return (min(supports, key=lambda band: max(Decimal(0), price-band[1]), default=None),
            min(resistances, key=lambda band: max(Decimal(0), band[0]-price), default=None))


def plain_direction(value):
    raw = str(value or '').upper()
    if any(word in raw for word in ('BEARISH', 'DOWN', 'BREAKDOWN', 'SHORT')):
        return 'downward'
    if any(word in raw for word in ('BULLISH', 'UP', 'BREAKOUT', 'LONG')):
        return 'upward'
    if raw in {'RANGE', 'RANGING', 'CHOPPY'}:
        return 'sideways'
    return 'unclear'


def signal_state(signals):
    active = []
    for row in signals:
        raw = str(row.get('display_status') or row.get('lifecycle_status') or row.get('status') or row.get('entry_status') or '').upper()
        if raw in {'OPEN', 'ACTIVE', 'TAKEN', 'TRIGGERED', 'ENTERED', 'TP1_HIT', 'TP1_HIT_TP2_RUNNING', 'READY', 'PENDING', 'PENDING_ENTRY', 'WAITING', 'WAITING_ENTRY'}:
            active.append(raw)
    if not active:
        return 'no active signal'
    if all(raw in {'READY', 'PENDING', 'PENDING_ENTRY', 'WAITING', 'WAITING_ENTRY'} for raw in active):
        return 'signal active, awaiting entry'
    if 'TP1_HIT' in active or 'TP1_HIT_TP2_RUNNING' in active:
        return 'signal active, first target reached'
    return 'signal active'


def single_signal_state(row):
    raw = str(row.get('display_status') or row.get('lifecycle_status') or row.get('status') or row.get('entry_status') or '').upper()
    states = {'TP2_HIT': 'final target reached', 'TP_HIT': 'target reached',
              'STOPPED': 'stopped', 'SL_HIT': 'stopped', 'PARTIAL_LOSS': 'stopped',
              'BREAK_EVEN': 'stopped', 'CLOSED_AFTER_TP1': 'closed after first target',
              'TIME_EXIT': 'closed at time limit', 'EXPIRED_POSITION': 'closed at time limit',
              'EXPIRED_RUNNER': 'closed at time limit', 'CLOSED': 'closed',
              'CANCELLED': 'cancelled', 'EXPIRED': 'expired', 'UNRESOLVED': 'status unresolved'}
    return f'no active signal; {states[raw]}' if raw in states else signal_state([row])


def presentation(data, *, observed_at):
    kind, symbol = data.get('market_type', 'crypto'), str(data.get('symbol') or 'SwiftChart')
    precision = data.get('price_format') or {}
    formatted = lambda value: price_text(value, kind, symbol, precision)
    price = formatted(data.get('current_price'))
    support_band, resistance_band = nearest_zones(data)
    def band(value):
        if value is None:
            return None
        low, high = map(formatted, value)
        return f'{low}–{high}' if low is not None and high is not None else None
    support, resistance = band(support_band), band(resistance_band)
    trend = plain_direction(data.get('market_condition') or data.get('regime') or data.get('bias'))
    bias = plain_direction(data.get('bias') or data.get('market_condition'))
    higher = plain_direction(data.get('htf_context') or data.get('higher_timeframe_bias'))
    signal = single_signal_state(data['single_signal']) if data.get('single_signal') else signal_state(data.get('signals', []))
    try:
        as_of = datetime.fromisoformat(observed_at.replace('Z', '+00:00')).astimezone(UTC).strftime('%Y-%m-%d %H:%M UTC')
    except (AttributeError, ValueError):
        as_of = 'time unavailable'
    price = price or 'unavailable'
    current = decimal(data.get('current_price'))
    support_relation = 'price inside zone' if support_band and current < support_band[1] else 'at or below price'
    resistance_relation = 'price inside zone' if resistance_band and current > resistance_band[0] else 'at or above price'
    support_text = f'{support} ({support_relation})' if support else 'none confirmed below price'
    resistance_text = f'{resistance} ({resistance_relation})' if resistance else 'none confirmed above price'
    action = 'Signal active; monitor the published signal' if signal.startswith('signal active') else 'Wait for a confirmed signal'
    # Allowed factual clauses are produced here, not calculated in the agent.
    # Two wording choices leave composition to the model while making semantic
    # validation as strict as numerical validation (including swapped levels).
    options = [
        [f'{symbol} · {str(data.get("timeframe") or "").upper()} · {bias} bias · {signal}',
         f'{symbol} | {str(data.get("timeframe") or "").upper()} | Bias: {bias} | {signal}'],
        [f'Price: {price}. Trend: {trend}. Higher timeframe: {higher}.',
         f'Price {price}; trend {trend}; higher timeframe {higher}.'],
        [f'Support: {support_text}; resistance: {resistance_text}.',
         f'Nearest support: {support_text}. Nearest resistance: {resistance_text}.'],
        [f'Now: {action}.', f'What to do: {action}.'],
        [f'Data: {as_of}. Not financial advice.'],
    ]
    result = {'symbol': symbol, 'price': price, 'support': support, 'resistance': resistance,
            'bias': bias, 'trend': trend, 'higher_timeframe': higher, 'signal': signal,
            'as_of': as_of, 'line_options': options}
    structure = {'HH/HL': 'higher highs and higher lows', 'LH/LL': 'lower highs and lower lows',
                 'Range': 'a respected trading range', 'Mixed / Choppy': 'mixed, choppy structure'}.get(data.get('structure'))
    if structure is None:
        structure = {'upward': 'recent structure favours buyers', 'downward': 'recent structure favours sellers',
                     'sideways': 'price is moving in a range', 'unclear': 'structure has no clear direction'}[trend]
    else:
        structure = 'price is forming ' + structure
    up = formatted(resistance_band[1]) if resistance_band else None
    down = formatted(support_band[0]) if support_band else None
    upside = f'A break and hold above {up} would support an upward move' if up else 'There is no confirmed upper boundary for a priced breakout scenario'
    downside = f'A break and hold below {down} would support further weakness' if down else 'There is no confirmed lower boundary for a priced breakdown scenario'
    if bias == 'downward' and up:
        change = f'The downward view weakens if price breaks and holds above {up}'
    elif bias == 'upward' and down:
        change = f'The upward view weakens if price breaks and holds below {down}'
    else:
        change = 'A clear break and hold outside the nearest zones would change the picture'
    below = formatted(resistance_band[0]) if resistance_band else down
    hold = f'Holding below {below} keeps the downward scenario in play' if below else 'Wait for fresh structure before assigning a lower boundary'
    brief = [
        [f'{symbol} {str(data.get("timeframe") or "").upper()}: {bias} bias because {structure}; {signal}.',
         f'{symbol} {str(data.get("timeframe") or "").upper()} has a {bias} bias: {structure}. {signal.capitalize()}.'],
        [f'Price: {price}. Support: {support_text}; resistance: {resistance_text}.',
         f'Price: {price}. Nearest support: {support_text}; nearest resistance: {resistance_text}.'],
        [change + '.'],
        [f'Now: {action}. Data: {as_of}. Not financial advice.'],
    ]
    companion = (data.get('companion') or {}).get('reply') or {}
    companion_line = (f'{companion["timeframe"]} context: {companion["trend"]} trend; {companion["structure"]}.'
                      if companion.get('timeframe') else 'Companion timeframe context is not available yet; no direction is assumed.')
    detailed = [options[0], [f'Current API price: {price}.'],
                [f'{str(data.get("timeframe") or "").upper()} trend: {trend}; {structure}.'],
                [companion_line], options[2], [upside + '.'], [hold + '. ' + downside + '.'],
                ['View invalidation: ' + change[0].lower() + change[1:] + '.'],
                [f'Now: {action}. Data: {as_of}. Not financial advice.']]
    result.update(timeframe=str(data.get('timeframe') or '').upper(), structure=structure,
                  view_change=change, upside=upside, downside=downside,
                  trigger_above=up, trigger_below=down,
                  views={'default': brief, 'detail': detailed})
    return result


def chart_projection(data, *, observed_at):
    """All plotted market labels and axis numbers are API-owned facts."""
    candles = [{k: row[k] for k in ('timestamp','open','high','low','close') if k in row}
               for row in data.get('chart_candles', [])[-100:]]
    if len(candles) < 10:
        return {'available': False, 'symbol': data.get('symbol'), 'message': 'Candle history is unavailable.'}
    kind, symbol = data['market_type'], data['symbol']
    fmt = lambda value: price_text(value,kind,symbol,data.get('price_format') or {})
    if fmt(data.get('current_price')) is None:
        return {'available': False, 'symbol': symbol, 'message':'Verified price precision is unavailable.'}
    support, resistance = nearest_zones(data)
    levels = []
    for label, value, colour in [('Support',support,'#3ddc97'),('Resistance',resistance,'#ff646d')]:
        if value:
            levels.append({'label':label,'low':float(value[0]),'high':float(value[1]),
                           'text':f'{label} {fmt(value[0])}–{fmt(value[1])}','colour':colour})
    signals = data.get('signals', [])
    for row in signals[:1]:
        if not signal_state([row]).startswith('signal active'):
            continue
        entry = row.get('entry_price')
        pairs = [('Entry',entry),('Stop',row.get('stop_loss')),('Target 1',row.get('take_profit_1')),('Target 2',row.get('take_profit_2'))]
        if not entry:
            zone = row.get('entry_zone') or [row.get('entry_zone_low'),row.get('entry_zone_high')]
            pairs[:1] = [('Entry low',zone[0]),('Entry high',zone[1])]
        for label, price in pairs:
            if decimal(price) and fmt(price):
                levels.append({'label':label,'low':float(price),'high':float(price),'text':f'{label} {fmt(price)}','colour':'#c7a7ff' if label.startswith('Entry') else '#f2c46d'})
    current = float(data['current_price'])
    levels.append({'label':'Current','low':current,'high':current,'text':'Current '+str(fmt(current)), 'colour':'#6fd0d4'})
    values = [float(row[key]) for row in candles for key in ('low','high')] + [v for level in levels for v in (level['low'],level['high'])]
    low, high = min(values), max(values)
    padding = max((high-low)*.05, high*.001)
    low, high = max(low-padding, low*.5), high+padding
    ticks = [{'value': low+(high-low)*i/5, 'text':fmt(low+(high-low)*i/5)} for i in range(6)]
    times = []
    for index in sorted({0,len(candles)//4,len(candles)//2,len(candles)*3//4,len(candles)-1}):
        value = candles[index]['timestamp']
        time = datetime.fromisoformat(str(value).replace('Z','+00:00')) if isinstance(value,str) else value
        times.append({'index':index,'text':time.astimezone(UTC).strftime('%d %b %H:%M')})
    facts = presentation(data,observed_at=observed_at)
    return {'available':True,'symbol':symbol,'timeframe':str(data['timeframe']).upper(),
            'candles':candles,'candle_count':len(candles),'levels':levels,'axis_min':low,'axis_max':high,'axis_ticks':ticks,
            'time_ticks':times,'as_of':facts['as_of'],
            'palette':{'background':'#070908','panel':'#151513','text':'#e2e2db','muted':'#939b97','grid':'#292d2a','up':'#3ddc97','down':'#ff646d'}}
