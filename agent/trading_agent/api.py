"""GET-only, loopback-only HTTP client with bounded, coalesced short caching."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import UTC, datetime
import ipaddress
import json
import re
from time import monotonic
from urllib.parse import urlsplit

import httpx


class APIUnavailable(RuntimeError):
    pass


PERFORMANCE_KEY = re.compile(
    r"(?:win.?rate|performance|profit|pnl|drawdown|expectancy|equity|realized|unrealized|"
    r"risk_reward|reward_risk|(?:^|_)r(?:_|$)|(?:^|_)(?:wins|losses|totals?|counts?|stats|results?)(?:_|$))",
    re.I,
)


def without_performance(value):
    if isinstance(value, dict):
        return {k: without_performance(v) for k, v in value.items() if k in {'take_profit_1', 'take_profit_2', 'candle_count'} or not PERFORMANCE_KEY.search(k)}
    if isinstance(value, list):
        return [without_performance(v) for v in value]
    return value


class SwiftChartAPI:
    def __init__(self, base_url, token, *, transport=None, ttl=20, timeout=20):
        url = urlsplit(base_url)
        try:
            local = ipaddress.ip_address(url.hostname or '').is_loopback
        except ValueError:
            local = url.hostname == 'localhost'
        if url.scheme != 'http' or not local or url.username or url.password or url.path not in ('', '/') or url.query or url.fragment:
            raise ValueError('SwiftChart API must be a direct loopback HTTP origin without credentials or a path')
        if not token:
            raise ValueError('AGENT_INTERNAL_API_TOKEN is required')
        self.ttl = min(20.0, max(0.0, float(ttl)))
        self.client = httpx.AsyncClient(
            base_url=base_url.rstrip('/'), transport=transport, timeout=timeout,
            headers={'X-Agent-Token': token}, trust_env=False, follow_redirects=False,
            limits=httpx.Limits(max_connections=2, max_keepalive_connections=2),
        )
        self._cache = {}
        self._pending = {}
        self._slots = asyncio.Semaphore(2)

    async def _fetch(self, path, params):
        async with self._slots:
            for attempt in range(3):
                try:
                    response = await self.client.get('/internal/agent' + path, params=params)
                    if response.status_code == 503 and attempt < 2:
                        await asyncio.sleep(0.15 * (attempt + 1))
                        continue
                    if response.status_code == 404:
                        return {'available': False, 'message': 'Not covered by SwiftChart.'}
                    if response.status_code != 200:
                        raise APIUnavailable('SwiftChart data is temporarily unavailable')
                    if len(response.content) > 2_000_000:
                        raise APIUnavailable('SwiftChart response exceeds the safe size limit')
                    value = without_performance(response.json())
                    if not isinstance(value, dict) or value.get('schema_version') != 'agent-v1' or not isinstance(value.get('data'), dict):
                        raise APIUnavailable('SwiftChart API contract mismatch')
                    return {**value['data'], 'observed_at': value.get('observed_at'), 'source': value.get('source')}
                except (httpx.HTTPError, ValueError) as exc:
                    raise APIUnavailable('SwiftChart data is temporarily unavailable') from None

    async def get(self, path, *, max_age=None, **params):
        if not re.fullmatch(r'/[A-Za-z0-9_/-]+', path) or '..' in path:
            raise ValueError('Invalid read-only API path')
        params = {k: v for k, v in params.items() if v is not None}
        key = (path, json.dumps(params, sort_keys=True))
        cached = self._cache.get(key)
        if cached and monotonic() - cached[0] < min(self.ttl, self.ttl if max_age is None else max_age):
            return deepcopy(cached[1])
        task = self._pending.get(key)
        if task is None:
            task = asyncio.create_task(self._fetch(path, params))
            self._pending[key] = task
            def complete(done):
                self._pending.pop(key, None)
                if not done.cancelled() and done.exception() is None:
                    if len(self._cache) >= 256:
                        self._cache.pop(next(iter(self._cache)))
                    result = done.result()
                    try:
                        observed = datetime.fromisoformat(result['observed_at'].replace('Z', '+00:00'))
                        age = max(0, (datetime.now(UTC) - observed).total_seconds())
                    except (KeyError, ValueError, TypeError):
                        age = self.ttl
                    # Do not stack a second full TTL on an already cached API response.
                    self._cache[key] = (monotonic() - age, result)
            task.add_done_callback(complete)
        return deepcopy(await asyncio.shield(task))

    async def close(self):
        await self.client.aclose()
