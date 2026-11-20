"""Bounded conversation JSON files only. No market database or SQLite dependency."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
from threading import RLock


class ConversationStore:
    def __init__(self, path, *, max_messages=40):
        self.path = Path(path)
        self.path.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path.chmod(0o700)
        self.max_messages = max_messages
        self._lock = RLock()

    def _file(self, key):
        return self.path / (hashlib.sha256(key.encode()).hexdigest() + '.json')

    def get_history(self, session_key):
        with self._lock:
            try:
                file = self._file(session_key)
                if file.stat().st_size > 200_000:
                    return []
                data = json.loads(file.read_text())
                return data[-self.max_messages:] if isinstance(data, list) else []
            except (FileNotFoundError, ValueError):
                return []

    def replace_history(self, session_key, messages):
        with self._lock:
            bounded = [{'role': m['role'], 'content': str(m.get('content', ''))[:2000]} for m in messages[-self.max_messages:]]
            fd, name = tempfile.mkstemp(dir=self.path, prefix='.session-')
            try:
                with os.fdopen(fd, 'w') as stream:
                    json.dump(bounded, stream)
                os.replace(name, self._file(session_key))
            finally:
                Path(name).unlink(missing_ok=True)

    def reset(self, session_key):
        with self._lock:
            self._file(session_key).unlink(missing_ok=True)
