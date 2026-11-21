import asyncio
from dataclasses import replace
from datetime import datetime, UTC
import json
from pathlib import Path

import httpx
import pytest

from test_agent_api import settings
from trading_agent.grok import GrokContextClient
from trading_agent.service import TradingAgentService, AgentReply
from trading_agent.usage import DailyQuota, QuestionUsage, _CURRENT, start_call, record_call
from trading_agent.writer import GrokWriter, WriterRouter


def run(coro):return asyncio.run(coro)


def test_grok_models_are_configurable_and_writer_cannot_search_or_send_history(tmp_path):
    async def scenario():
        calls=[]
        async def handle(request):
            body=json.loads(request.content);calls.append(body)
            assert body['tools']==[] and body['store'] is False
            assert 'personal history' not in json.dumps(body)
            return httpx.Response(200,json={'output_text':'API-only reply','usage':{'input_tokens':100,'output_tokens':25,'input_tokens_details':{'cached_tokens':80},'cost_in_usd_ticks':123456}})
        client=httpx.AsyncClient(transport=httpx.MockTransport(handle))
        selected=settings(tmp_path,writer_default_model='configured-default',writer_detail_model='configured-detail')
        writer=GrokWriter(selected,client=client)
        for detail in (False,True):
            await writer.respond(message='Copy supplied API text',smart_context='API facts',history=[{'content':'personal history'}],detail=detail)
        assert [c['model']for c in calls]==['configured-default','configured-detail']
        assert 'reasoning'not in calls[0] and calls[1]['reasoning']=={'effort':'none'}
        await client.aclose()
    run(scenario())


def test_bedrock_fallback_is_never_loaded_unless_enabled(tmp_path):
    class Failed:
        async def respond(self,**kwargs):raise RuntimeError('xAI unavailable')
    loaded=[]
    class Bedrock:
        async def respond(self,**kwargs):
            assert 'detail'not in kwargs
            return {'final_response':'fallback'}
    def factory():loaded.append(True);return Bedrock()
    async def scenario():
        router=WriterRouter(settings(tmp_path),primary=Failed(),bedrock_factory=factory)
        with pytest.raises(RuntimeError):await router.respond(message='API facts',detail=True)
        assert not loaded
        enabled=WriterRouter(settings(tmp_path,bedrock_fallback_enabled=True),primary=Failed(),bedrock_factory=factory)
        assert (await enabled.respond(message='API facts',detail=True))['final_response']=='fallback'
        assert len(loaded)==1
    run(scenario())


def test_usage_logs_actual_billed_cost_and_late_completion_without_content(tmp_path):
    trace=QuestionUsage(tmp_path/'usage','text');token=_CURRENT.set(trace)
    try:
        call=start_call();trace.finish('template_or_control')
        record_call(call,provider='xai',model='configured-model',seconds=.25,
                    usage={'input_tokens':100,'output_tokens':25,'cost_in_usd_ticks':123456,'server_side_tool_usage_details':{'x_posts_fetched':9}})
    finally:_CURRENT.reset(token)
    rows=[json.loads(line)for file in (tmp_path/'usage').glob('*.jsonl')for line in file.read_text().splitlines()]
    assert rows[0]['cost_usd']is None and rows[0]['pending_calls']==1
    assert rows[-1]['cost_usd']==pytest.approx(.0000123456) and rows[-1]['x_overfetch']is True
    assert rows[-1]['input_tokens']==100 and rows[-1]['output_tokens']==25
    assert all('prompt'not in row and 'user_id'not in row and 'reply'not in row for row in rows)
    assert all(file.stat().st_mode&0o077==0 for file in (tmp_path/'usage').iterdir())


def test_quota_persists_per_user_across_chats_restart_and_lagos_midnight(tmp_path):
    quota=DailyQuota(tmp_path/'quota',limit=30)
    before=datetime(2026,11,20,22,59,tzinfo=UTC)
    assert all(quota.take(42,now=before)for _ in range(30))
    assert not DailyQuota(tmp_path/'quota').take(42,now=before)
    assert quota.take(43,now=before)
    assert quota.take(42,now=datetime(2026,11,20,23,0,tzinfo=UTC))


def test_thirty_first_question_cannot_reach_the_api_and_reset_does_not_reset_quota(tmp_path):
    service=TradingAgentService(settings(tmp_path),orchestrator=object())
    seen=[]
    async def answer(**kwargs):seen.append(1);return AgentReply('safe reply')
    service._handle_turn=answer
    async def scenario():
        replies=await asyncio.gather(*(service.handle_turn(chat_id=i,user_id=42,text='BTC')for i in range(31)))
        assert len(seen)==30 and sum('daily limit'in r.text for r in replies)==1
        service.reset(chat_id=1,user_id=42)
        assert 'daily limit'in (await service.handle_turn(chat_id=1,user_id=42,text='BTC')).text
    run(scenario())


def test_news_is_coalesced_and_cached_per_coin_with_eight_retained_x_posts(tmp_path,monkeypatch):
    async def scenario():
        calls=[];clock=[10]
        monkeypatch.setattr('trading_agent.grok.monotonic',lambda:clock[0])
        async def handle(request):
            payload=json.loads(request.content);calls.append(payload)
            assert 'limit=8'in payload['instructions'] and payload['store']is False
            await asyncio.sleep(.005)
            return httpx.Response(200,json={'output_text':'Market sentiment remains cautious.','citations':[{'url':f'https://x.com/feed/status/{i}'}for i in range(12)]})
        def factory(**kw):return httpx.AsyncClient(transport=httpx.MockTransport(handle),**kw)
        client=GrokContextClient(settings(tmp_path),client_factory=factory)
        assert await client.fetch('BTC levels',coin='BTCUSDT')is None
        out=await asyncio.gather(*(client.fetch('BTC news',coin='BTCUSDT')for _ in range(8)))
        assert len(calls)==1 and len(out[0].sources)==8
        clock[0]=909
        assert await client.fetch('other BTC headlines',coin='BTCUSDT')==out[0]
        assert len(calls)==1
        await client.fetch('ETH news',coin='ETHUSDT');assert len(calls)==2
        clock[0]=910
        await client.fetch('BTC news',coin='BTCUSDT');assert len(calls)==3
    run(scenario())


def test_xai_only_bot_settings_do_not_require_bedrock_credentials(tmp_path):
    selected=settings(tmp_path,aws_region='',bedrock_model_id='')
    selected.validate_for_bot()
    with pytest.raises(RuntimeError):replace(selected,bedrock_fallback_enabled=True).validate_for_bot()
