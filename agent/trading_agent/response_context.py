"""Validate model-written API display facts; never calculate market numbers."""
import json
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
    if re.search(r'\b[A-Z][A-Z0-9]*_[A-Z0-9_]+\b', text):
        return ''
    if news and re.search(r'\b(buy|sell|entry|target|stop.loss|setup|confirmed|invalidated|take.profit)\b', text, re.I):
        return ''
    return text[:1800]


def render_signal(data):
    return render_snapshot(data)


def render_snapshot(data):
    options = (data.get('reply') or {}).get('line_options')
    if not isinstance(options, list) or len(options) != 5 or not all(isinstance(row, list) and row and all(isinstance(item, str) for item in row) for row in options):
        return f'{data.get("symbol", "SwiftChart")}\nSwiftChart display facts are unavailable. Please retry shortly.\nNot financial advice.'
    return '\n'.join(row[0] for row in options)


NUMBER = re.compile(r'\d+(?:[.,]\d+)*(?:[eE][+-]?\d+)?')


def checked_reply(text, data):
    """Check every number, then line placement and meaning. Fail closed.

    Numeric inclusion alone would permit a model to swap support/resistance or
    call an unpublished setup active. API-provided line choices prevent both.
    """
    text = str(text or '').strip()
    facts = data.get('reply') or {}
    allowed_numbers = set(NUMBER.findall(json.dumps(facts, ensure_ascii=False)))
    if any(number not in allowed_numbers for number in NUMBER.findall(text)):
        return render_snapshot(data), False, 'ungrounded_number'
    lines, options = text.splitlines(), facts.get('line_options') or []
    if len(lines) != 5 or len(options) != 5 or any(line not in choices for line, choices in zip(lines, options)):
        return render_snapshot(data), False, 'shape_or_fact_mismatch'
    return text, True, None
