import asyncio
import json

from fastapi import FastAPI
import httpx
import pytest

from app.routes.agent_internal import router
from app.services import agent_data


def request(monkeypatch, *, peer='127.0.0.1', headers=None, token='test-internal-token', path='/internal/agent/markets', method='GET'):
    monkeypatch.setenv('AGENT_INTERNAL_API_TOKEN',token)
    async def fake():
        return agent_data.envelope({'crypto':[],'forex':[]},'test')
    monkeypatch.setattr(agent_data,'markets',fake)
    app=FastAPI();app.include_router(router)
    async def check():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app,client=(peer,1234)),base_url='http://api') as client:
            return await client.request(method,path,headers=headers or {})
    return asyncio.run(check())


def test_missing_token_denied(monkeypatch):
    assert request(monkeypatch).status_code==403


def test_unconfigured_token_denied(monkeypatch):
    assert request(monkeypatch,token='',headers={'x-agent-token':'test-internal-token'}).status_code==403


def test_valid_local_token_succeeds_and_is_not_cacheable(monkeypatch):
    response=request(monkeypatch,headers={'x-agent-token':'test-internal-token'})
    assert response.status_code==200 and response.headers['cache-control']=='no-store'


@pytest.mark.parametrize('header',['x-forwarded-for','x-real-ip','forwarded','x-forwarded-host','x-forwarded-proto'])
def test_proxy_request_denied_even_with_real_token_and_loopback_peer(monkeypatch,header):
    assert request(monkeypatch,headers={'x-agent-token':'test-internal-token',header:'127.0.0.1'}).status_code==403


def test_remote_peer_denied_even_with_real_token(monkeypatch):
    assert request(monkeypatch,peer='192.0.2.20',headers={'x-agent-token':'test-internal-token'}).status_code==403


def test_write_methods_and_public_openapi_are_unavailable(monkeypatch):
    assert request(monkeypatch,headers={'x-agent-token':'test-internal-token'},method='POST').status_code==405
    app=FastAPI();app.include_router(router)
    assert not app.openapi().get('paths')


def test_signal_projection_cannot_leak_performance_or_private_metadata():
    result=agent_data.signal_projection({'id':7,'symbol':'SOLUSDT','status':'TIME_EXIT',
        'entry_price':2,'pnl_r':0.7,'win_rate':99,'result':'WIN','count':15,
        'secret':'private','user_id':123,'nested':{'performance':10}})
    assert result=={'id':7,'symbol':'SOLUSDT','status':'TIME_EXIT','entry_price':2}


def test_cache_never_queues_or_runs_a_scan(monkeypatch):
    from app.services import scanner
    def no_refresh(*args,**kwargs):
        raise AssertionError('Read-only scan must not refresh')
    monkeypatch.setattr(scanner,'trigger_top_ideas_refresh',no_refresh)
    monkeypatch.setattr(scanner,'_scan_cache',{})
    assert scanner.read_cached_top_ideas('hyperliquid','4h')['ideas']==[]


def test_server_singleflight_and_work_budget():
    async def check():
        release=asyncio.Event()
        async def work():
            await release.wait()
            return {'same':'facts'}
        first=asyncio.create_task(agent_data.cached(('first',),work))
        second=asyncio.create_task(agent_data.cached(('second',),work))
        await asyncio.sleep(0)
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as err:
            await agent_data.cached(('third',),work)
        assert err.value.status_code==503
        release.set()
        assert await first==await second
    asyncio.run(check())


def test_crypto_snapshot_uses_published_status_not_new_candidate_trade(monkeypatch):
    async def resolved(kind,symbol):return 'SOLUSDT'
    async def analysis(**kwargs):
        return {'symbol':'SOLUSDT','current_price':123.4,'trade_ideas':[{'entry_price':999,'status':'OPEN'}],
                'support_zones':[{'type':'support','lower':120,'upper':121,'win_rate':95}], 'resistance_zones':[]}
    async def active(kind,symbol):
        return {'data':{'crypto':[{'id':7,'status':'TP1_HIT','entry_price':122,'timeframe':'4h'}]}}
    monkeypatch.setattr(agent_data,'require_symbol',resolved)
    monkeypatch.setattr(agent_data,'analyze_market_read_only',analysis)
    monkeypatch.setattr(agent_data,'active',active)
    data=asyncio.run(agent_data.snapshot('crypto','SOL','4h'))['data']
    assert data['signals']==[{'id':7,'status':'TP1_HIT','entry_price':122,'timeframe':'4h'}]
    assert '999' not in json.dumps(data) and 'win_rate' not in json.dumps(data)


def test_forex_snapshot_preserves_website_zone_and_setup_facts(monkeypatch):
    async def resolved(kind,symbol):return 'EURUSD'
    original={'symbol':'EURUSD','timeframe':'1H','current_price':1.123456,'decision':'WAIT_FOR_RETEST',
              'levels':{'confirmation':1.125,'retest':1.124,'invalidation':1.12,'target_1':1.13,'target_2':1.14},
              'buy_zones':[{'low':1.121,'high':1.122,'state':'RETESTING','successful_reactions':12}], 'sell_zones':[]}
    async def chart(**kwargs):return original
    monkeypatch.setattr(agent_data,'require_symbol',resolved)
    async def active(kind,symbol):return {'data':{'forex':[]}}
    monkeypatch.setattr(agent_data,'active',active)
    monkeypatch.setattr(agent_data,'forex_pair_chart',chart)
    data=asyncio.run(agent_data.snapshot('forex','EURUSD','1H'))['data']
    assert data['levels']==original['levels']
    assert data['decision']==original['decision']
    assert data['current_price']==original['current_price']
    assert data['buy_zones']==[{'low':1.121,'high':1.122,'state':'RETESTING'}]


@pytest.mark.parametrize('raw,expected',[('active','OPEN'),('tp1_hit','TP1_HIT_TP2_RUNNING'),('SL_HIT','STOPPED'),('CLOSED_AFTER_TP1','STOPPED'),('TIME_EXIT','TIME_EXIT'),('UNRESOLVED','UNRESOLVED')])
def test_crypto_display_uses_lifecycle_facts_not_legacy_pnl(raw,expected):
    row=agent_data.signal_projection({'status':'OPEN','lifecycle_status':raw,'pnl_r':-4},market_type='crypto')
    assert row['display_status']==expected
    assert 'pnl_r' not in row


def test_catalog_preserves_all_scanned_coins_and_all_supported_forex_pairs(monkeypatch):
    from app.forex.config import SUPPORTED_FOREX_PAIRS
    rows=[{'symbol':f'COIN{i}USDT','exchange':'hyperliquid'} for i in range(250)]
    async def discover(exchange):return rows
    monkeypatch.setattr(agent_data,'discover_all_scan_markets',discover)
    catalog=asyncio.run(agent_data.markets())['data']
    assert [row['symbol'] for row in catalog['crypto']]==[row['symbol'] for row in rows]
    assert {row['symbol'] for row in catalog['forex']}==set(SUPPORTED_FOREX_PAIRS)


def test_a_ready_setup_is_not_invented_as_an_open_position():
    record=agent_data.signal_projection({'symbol':'SOLUSDT','entry_status':'READY','entry_zone':[120,121]},market_type='crypto')
    assert record['display_status']=='READY'
    assert record['entry_zone']==[120,121]
