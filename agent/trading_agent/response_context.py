"""Formatting only. Never calculate levels, infer a trade, or synthesize numbers."""
import re

PERFORMANCE_REQUEST = re.compile(
    r'\b(win.?rates?|win.?percentage|success.?rate|hit.?rate|track.?record|performance|pnl|profits?|drawdown|expectancy|returns?|statistics|stats|'
    r'total\s+(?:trades|wins|losses)|how many\s+(?:trades|wins|losses)|r[ -]?(?:results?|multiples?))\b', re.I)
NUMBER_WORD = re.compile(r'\b(zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand|million|billion|percent)\b', re.I)


def performance_request(text):
    return bool(PERFORMANCE_REQUEST.search(re.sub(r'\btake[ -]?profits?\b', 'target', text, flags=re.I)))


def safe_narrative(text, *, news=False):
    text = str(text or '').strip()
    if re.search(r'\d', text) or NUMBER_WORD.search(text) or performance_request(text) or '%' in text:
        return ''
    if news and re.search(r'\b(buy|sell|entry|target|stop.loss|setup|confirmed|invalidated|take.profit)\b', text, re.I):
        return ''
    return text[:1800]


def render_signal(data):
    fields = [('timeframe', 'Timeframe'), ('tracking_status', 'Tracking status'), ('lifecycle_phase', 'Lifecycle'),
              ('entry_status', 'Entry status'), ('entry_price', 'Entry'), ('stop_loss', 'Stop'),
              ('take_profit_1', 'TP1'), ('take_profit_2', 'TP2'), ('tracking_deadline', 'Tracking deadline')]
    lines = [str(data.get('symbol') or 'Signal')]
    status = data.get('display_status') or data.get('lifecycle_status') or data.get('tracking_status') or data.get('status')
    if status is not None:
        lines.append(f'Status: {status}')
    low = next((data[key] for key in ('entry_low','entry_zone_low') if data.get(key) is not None), None)
    high = next((data[key] for key in ('entry_high','entry_zone_high') if data.get(key) is not None), None)
    if (low is None or high is None) and isinstance(data.get('entry_zone'), (list,tuple)) and len(data['entry_zone']) == 2:
        low, high = data['entry_zone']
    if data.get('entry_price') is None and low is not None and high is not None:
        lines.append(f'Entry zone: {low}–{high}')
    for key, label in fields:
        if data.get(key) is not None:
            lines.append(f'{label}: {data[key]}')
    return '\n'.join(lines)


def render_snapshot(data):
    lines = [str(data.get('symbol', 'SwiftChart'))]
    if data.get('decision'):
        lines.append(f'Chart setup: {data["decision"]}')
    elif data.get('setup_status'):
        lines.append(f'Published setup: {data["setup_status"]}')
    for key, label in [('timeframe', 'Timeframe'), ('current_price', 'Price'), ('bias', 'Bias'), ('htf_context', 'Higher timeframe'), ('higher_timeframe_bias', 'Higher timeframe'), ('market_condition', 'Market state')]:
        if data.get(key) is not None:
            lines.append(f'{label}: {data[key]}')
    for key, label in [('confirmation', 'Confirmation'), ('retest', 'Retest'), ('invalidation', 'Invalidation'), ('target_1', 'Target'), ('target_2', 'Next target')]:
        value = (data.get('levels') or {}).get(key)
        if value is not None:
            lines.append(f'{label}: {value}')
    for key, label in [('buy_zones', 'Demand'), ('sell_zones', 'Supply'), ('support_zones', 'Support'), ('resistance_zones', 'Resistance')]:
        for zone in data.get(key, [])[:2]:
            low, high = zone.get('low', zone.get('lower')), zone.get('high', zone.get('upper'))
            if low is not None and high is not None:
                lines.append(f'{label}: {low}–{high}' + (f' ({zone["state"]})' if zone.get('state') else ''))
    for signal in data.get('signals', [])[:2]:
        lines.append('Published signal\n' + render_signal(signal))
    if data.get('setup_status') == 'NO_PUBLISHED_SIGNAL':
        lines.append('No published SwiftChart signal for this symbol and timeframe. Zones alone are not an entry.')
    if data.get('observed_at'):
        lines.append(f'SwiftChart snapshot: {data["observed_at"]}')
    return '\n'.join(lines)
