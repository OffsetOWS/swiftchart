"""Private request accounting without prompts, replies, identities or credentials."""
from contextvars import ContextVar
from datetime import datetime, UTC
import fcntl
import json
import os
from pathlib import Path
from time import perf_counter
from uuid import uuid4
from zoneinfo import ZoneInfo

from trading_agent.private_state import PrivateState

_CURRENT = ContextVar('agent_question_usage', default=None)


def _count(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


class QuestionUsage:
    def __init__(self, directory, kind):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.directory.chmod(0o700)
        self.identity = uuid4().hex
        self.kind = kind
        self.started = perf_counter()
        self.calls = []
        self.pending = 0
        self.finished = False
        self.elapsed = None
        self.outcome = 'running'

    def write(self, event):
        path = self.directory / (datetime.now(UTC).strftime('%Y-%m-%d') + '.jsonl')
        lock_fd = os.open(self.directory / '.usage.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(lock_fd, 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if path.exists() and path.stat().st_size > 5_000_000:
                os.replace(path, path.with_suffix('.previous.jsonl'))
            fd = os.open(path, os.O_CREAT | os.O_APPEND | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'a') as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(json.dumps({'at': datetime.now(UTC).isoformat(), 'question_id': self.identity, **event}, separators=(',', ':')) + '\n')

    def summary(self):
        unknown = self.pending > 0 or any(c['cost_usd'] is None for c in self.calls)
        news = [c for c in self.calls if c['provider'] == 'xai-news']
        unknown_posts = any(c['x_posts_fetched'] is None for c in news)
        return {'kind': self.kind, 'reply_seconds': self.elapsed, 'outcome': self.outcome,
                'provider_calls': len(self.calls), 'pending_calls': self.pending,
                'input_tokens': sum(c['input_tokens'] or 0 for c in self.calls),
                'output_tokens': sum(c['output_tokens'] or 0 for c in self.calls),
                'cached_input_tokens': sum(c['cached_input_tokens'] or 0 for c in self.calls),
                'tokens_complete': not self.pending and all(c['input_tokens'] is not None and c['output_tokens'] is not None for c in self.calls),
                'cost_usd': None if unknown else sum(c['cost_usd'] for c in self.calls),
                'cost_status': 'pending_or_unknown' if unknown else ('provider_reported' if self.calls else 'no_provider_call'),
                'models': list(dict.fromkeys(c['model'] for c in self.calls)),
                'x_posts_fetched': None if unknown_posts else sum(c['x_posts_fetched'] or 0 for c in news),
                'x_overfetch': True if any(c['x_overfetch'] for c in self.calls) else (None if unknown_posts else False)}

    def finish(self, outcome):
        self.finished = True
        self.outcome = outcome
        self.elapsed = perf_counter() - self.started
        self.write({'event': 'question', **self.summary()})


def start_call():
    trace = _CURRENT.get()
    if trace:
        trace.pending += 1
    return trace


def record_call(trace, *, provider, model, seconds, usage=None, status='completed', x_limit=8):
    usage = usage or {}
    details = usage.get('input_tokens_details') or {}
    tools = usage.get('server_side_tool_usage_details') or {}
    ticks = _count(usage.get('cost_in_usd_ticks'))
    call = {'provider': provider, 'model': model, 'seconds': seconds, 'status': status,
            'input_tokens': _count(usage.get('input_tokens', usage.get('prompt_tokens'))),
            'output_tokens': _count(usage.get('output_tokens', usage.get('completion_tokens'))),
            'cached_input_tokens': _count(details.get('cached_tokens')),
            'cost_usd': ticks / 10_000_000_000 if ticks is not None else None,
            'cost_in_usd_ticks': ticks, 'x_posts_fetched': _count(tools.get('x_posts_fetched')),
            'x_users_fetched': _count(tools.get('x_users_fetched')),
            'x_overfetch': (_count(tools.get('x_posts_fetched')) or 0) > x_limit}
    if trace:
        trace.pending -= 1
        trace.calls.append(call)
        trace.write({'event': 'provider_call', **call})
        if trace.finished:
            trace.write({'event': 'question_usage_update', **trace.summary()})
    return call


class DailyQuota:
    def __init__(self, directory, limit=30, timezone='Africa/Lagos'):
        self.state = PrivateState(directory)
        self.limit = limit
        self.timezone = ZoneInfo(timezone)

    def take(self, user_id, *, now=None):
        day = (now or datetime.now(UTC)).astimezone(self.timezone).date().isoformat()
        with self.state.transaction(str(user_id)) as data:
            count = data.get('count', 0) if data.get('day') == day else 0
            if count >= self.limit:
                return False
            data.update(day=day, count=count + 1)
        return True
