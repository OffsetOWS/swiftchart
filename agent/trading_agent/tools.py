from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any, Awaitable, Callable

from trading_agent.api import SwiftChartAPI
from trading_agent.config import TradingAgentSettings

TOOLSET_NAME = 'swiftchart_readonly'


def tool_result(value):
    return json.dumps(value, ensure_ascii=False, default=str)


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict
    handler: Callable[[dict], Awaitable[Any]]

    @property
    def schema(self):
        return {'name': self.name, 'description': self.description, 'parameters': self.parameters}


class UnsupportedMarket(ValueError):
    pass


class SwiftChartReadOnlyTools:
    """No backend imports, exchange SDKs, calculations, DB reads, or write tools."""
    def __init__(self, api=None, settings=None):
        settings = settings or TradingAgentSettings.from_env()
        self.api = api or SwiftChartAPI(settings.api_base_url, settings.api_token,
                                        ttl=settings.cache_ttl_seconds,
                                        timeout=settings.tool_timeout_seconds)

    async def get_markets(self, args=None):
        return await self.api.get('/markets')

    @staticmethod
    def normalize(value):
        return re.sub(r'[^A-Z0-9]', '', str(value).upper())

    async def resolve(self, value, market_type=None):
        value = self.normalize(value)
        data = await self.get_markets()
        for kind in ('forex', 'crypto'):
            if market_type and kind != market_type:
                continue
            for item in data.get(kind, []):
                symbol = item['symbol']
                base = self.normalize(item.get('base_asset', ''))
                aliases = {self.normalize(symbol), base}
                if kind == 'crypto' and base:
                    aliases.update({base + 'USD', base + 'USDC', base + 'PERP'})
                if value and value in aliases:
                    return kind, symbol
        if not data.get('crypto') and market_type != 'forex':
            from trading_agent.api import APIUnavailable
            raise APIUnavailable('SwiftChart crypto coverage is temporarily unavailable')
        covered = ', '.join(dict.fromkeys(item['symbol'] for kind in ('crypto', 'forex') for item in data.get(kind, [])))
        raise UnsupportedMarket(f'{value or "That symbol"} is not covered. Supported symbols: {covered or "temporarily unavailable"}.')

    async def scan_crypto(self, args):
        symbol = (await self.resolve(args['symbol'], 'crypto'))[1] if args.get('symbol') else None
        return await self.api.get('/scan', market_type='crypto', timeframe=args.get('timeframe', '4h'), symbol=symbol)

    async def scan_forex(self, args):
        symbol = (await self.resolve(args['symbol'], 'forex'))[1] if args.get('symbol') else None
        return await self.api.get('/scan', market_type='forex', timeframe=args.get('timeframe', '1H'), symbol=symbol)

    async def get_signal(self, args):
        kind = str(args.get('market_type', 'crypto'))
        identity = str(args.get('signal_id', args.get('id', '')))
        if kind not in ('crypto', 'forex') or not re.fullmatch(r'[A-Za-z0-9_-]{1,160}', identity):
            raise ValueError('A market type and valid signal ID are required')
        return await self.api.get(f'/signals/{kind}/{identity}')

    async def get_market_snapshot(self, args):
        kind, symbol = await self.resolve(args.get('symbol'), args.get('market_type'))
        return await self.api.get(f'/snapshot/{symbol}', market_type=kind,
                                  timeframe=args.get('timeframe', '1H' if kind == 'forex' else '4h'))

    async def get_currency_strength(self, args):
        return await self.api.get('/currency-strength', horizon=args.get('horizon', '24H'))

    async def get_volume_profile(self, args):
        kind, symbol = await self.resolve(args.get('symbol'), args.get('market_type'))
        return await self.api.get(f'/volume-profile/{symbol}', market_type=kind,
                                  timeframe=args.get('timeframe', '1H' if kind == 'forex' else '4h'))

    async def get_active_trades(self, args):
        kind = args.get('market_type', 'all')
        symbol = args.get('symbol')
        if symbol:
            kind, symbol = await self.resolve(symbol, None if kind == 'all' else kind)
        return await self.api.get('/active-trades', market_type=kind, symbol=symbol)

    def specs(self):
        common = {'symbol': {'type': 'string'}, 'market_type': {'type': 'string', 'enum': ['crypto', 'forex']}, 'timeframe': {'type': 'string'}}
        methods = [
            ('get_markets', 'List every currently supported market from SwiftChart.', {}),
            ('scan_crypto', 'Read the latest cached crypto scan; never start a scan.', common),
            ('scan_forex', 'Read persisted Forex signals; never start a scan.', common),
            ('get_signal', 'Read one signal and its canonical lifecycle status; no performance.', {'signal_id': {'type': 'string'}, **common}),
            ('get_market_snapshot', 'Read canonical price, zones, levels and setup status from the API.', common),
            ('get_currency_strength', 'Read canonical currency strength.', {'horizon': {'type': 'string'}}),
            ('get_volume_profile', 'Read the API-calculated informational volume profile.', common),
            ('get_active_trades', 'Read active canonical signals; no totals or performance.', common),
        ]
        return [ToolSpec(name, description, {'type': 'object', 'properties': props, 'additionalProperties': False}, getattr(self, name)) for name, description, props in methods]
