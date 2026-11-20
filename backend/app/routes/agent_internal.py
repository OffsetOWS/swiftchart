"""Internal agent reads: loopback peer AND token AND no reverse-proxy headers."""
import ipaddress
import os
import secrets
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from app.services import agent_data


async def authorize(request: Request, response: Response):
    try:
        loopback = ipaddress.ip_address(request.client.host).is_loopback
    except (ValueError, AttributeError):
        loopback = False
    # A public nginx request also has a loopback socket peer. Never trust that
    # alone, nor X-Forwarded-For as evidence of a local client.
    proxied = any(key.lower().startswith('x-forwarded-') or key.lower() in {'forwarded', 'x-real-ip'} for key in request.headers)
    expected = os.getenv('AGENT_INTERNAL_API_TOKEN', '')
    supplied = request.headers.get('x-agent-token', '')
    if not loopback or proxied or not expected or not supplied or not secrets.compare_digest(expected, supplied):
        raise HTTPException(403, 'Internal access denied')
    response.headers['Cache-Control'] = 'no-store'


router = APIRouter(prefix='/internal/agent', dependencies=[Depends(authorize)], include_in_schema=False)
Kind = Literal['crypto', 'forex']


@router.get('/prices')
async def prices(market_type: Kind, symbols: str = Query(max_length=1800)):
    return await agent_data.prices(market_type,symbols.split(','))


@router.get('/chart/{symbol}')
async def chart(symbol: str, market_type: Kind, timeframe: str = '4h'):
    if len(symbol)>40:raise HTTPException(422,'Invalid symbol')
    from app.services.market_data import interactive_candles
    with interactive_candles():
        return await agent_data.cached(('chart',market_type,symbol,timeframe),lambda:agent_data.chart(market_type,symbol,timeframe))


@router.get('/signal-context/{market_type}/{identity}')
async def signal_context(market_type: Kind, identity: str):
    if len(identity)>60:raise HTTPException(422,'Invalid signal identity')
    return await agent_data.cached(('signal-context',market_type,identity),lambda:agent_data.signal_context(market_type,identity))


@router.get('/markets')
async def markets():
    return await agent_data.cached(('markets',), agent_data.markets)


@router.get('/scan')
async def scan(market_type: Kind, timeframe: str = '4h', symbol: str | None = Query(None, max_length=40)):
    return await agent_data.cached(('scan', market_type, timeframe, symbol), lambda: agent_data.scan(market_type, timeframe, symbol))


@router.get('/signals/{market_type}/{identity}')
async def signal(market_type: Kind, identity: str):
    if len(identity) > 160:
        raise HTTPException(422, 'Invalid signal identity')
    return await agent_data.cached(('signal', market_type, identity), lambda: agent_data.signal(market_type, identity))


@router.get('/active-trades')
async def active(market_type: Literal['all', 'crypto', 'forex'] = 'all', symbol: str | None = Query(None, max_length=40)):
    return await agent_data.cached(('active', market_type, symbol), lambda: agent_data.active(market_type, symbol))


@router.get('/snapshot/{symbol}')
async def snapshot(symbol: str, market_type: Kind, timeframe: str = '4h', detail: bool = False):
    if len(symbol) > 40:
        raise HTTPException(422, 'Invalid symbol')
    from app.services.market_data import interactive_candles
    with interactive_candles():
        return await agent_data.cached(('snapshot', market_type, symbol, timeframe, detail), lambda: agent_data.snapshot(market_type, symbol, timeframe, detail=detail))


@router.get('/currency-strength')
async def strength(horizon: str = Query('24H', max_length=10)):
    return await agent_data.cached(('strength', horizon), lambda: agent_data.strength(horizon))


@router.get('/volume-profile/{symbol}')
async def volume(symbol: str, market_type: Kind, timeframe: str = '4h'):
    if len(symbol) > 40:
        raise HTTPException(422, 'Invalid symbol')
    return await agent_data.cached(('volume', market_type, symbol, timeframe), lambda: agent_data.volume(market_type, symbol, timeframe))
