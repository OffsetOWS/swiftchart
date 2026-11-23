"""Small owner-only JSON state; no market database and no SQLite dependency."""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import tempfile
from threading import RLock


class PrivateState:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.directory.chmod(0o700)
        self.lock = RLock()

    @contextmanager
    def transaction(self, key):
        name = hashlib.sha256(key.encode()).hexdigest()
        path = self.directory / (name + '.json')
        with self.lock:
            fd = os.open(self.directory / (name + '.lock'), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'r+') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                if path.is_symlink():
                    raise ValueError('State symlinks are not allowed')
                try:
                    if path.stat().st_size > 500_000:
                        raise ValueError('State exceeds its size limit')
                    data = json.loads(path.read_text())
                    if not isinstance(data, dict):
                        raise ValueError('Invalid state')
                except FileNotFoundError:
                    data = {}
                yield data
                handle, temporary = tempfile.mkstemp(dir=self.directory, prefix='.state-')
                try:
                    with os.fdopen(handle, 'w') as stream:
                        json.dump(data, stream, separators=(',', ':'))
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.replace(temporary, path)
                finally:
                    Path(temporary).unlink(missing_ok=True)


class MarketContexts:
    def __init__(self, directory):
        self.state = PrivateState(directory)

    def create(self, chat_id, user_id, market):
        import secrets
        from time import time
        token = secrets.token_urlsafe(9)
        with self.state.transaction(f'{chat_id}:{user_id}') as data:
            replies = {k: v for k, v in data.get('replies', {}).items() if time()-v['created'] < 7*86400}
            for key in list(replies)[:-49]:
                replies.pop(key)
            context = {k: market[k] for k in ('symbol', 'market_type', 'timeframe', 'signal_id') if market.get(k)}
            context.update(created=time(), detail=False)
            replies[token] = context
            data.update(replies=replies, last=token)
        return token

    def get(self, chat_id, user_id, token=None):
        from time import time
        with self.state.transaction(f'{chat_id}:{user_id}') as data:
            token = token or data.get('last')
            context = data.get('replies', {}).get(token)
            if context and time()-context['created'] < 7*86400:
                return dict(context, token=token)
        return None

    def update(self, chat_id, user_id, token, **changes):
        allowed = {'symbol', 'market_type', 'timeframe', 'signal_id', 'detail', 'awaiting_alert'}
        with self.state.transaction(f'{chat_id}:{user_id}') as data:
            context = data.get('replies', {}).get(token)
            if context is None:
                raise ValueError('That market reply has expired')
            context.update({k: v for k, v in changes.items() if k in allowed})
            data['last'] = token
        return self.get(chat_id, user_id, token)

    def reset(self, chat_id, user_id):
        with self.state.transaction(f'{chat_id}:{user_id}') as data:
            data.clear()
