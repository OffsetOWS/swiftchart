import asyncio
import ast
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from trading_agent.api import SwiftChartAPI, APIUnavailable
from trading_agent.config import TradingAgentSettings
from trading_agent.grok import GrokContextClient, needs_current_context
from trading_agent.hermes import HermesOrchestrator
from trading_agent.service import TradingAgentService
from trading_agent.session_store import ConversationStore
from trading_agent.tools import SwiftChartReadOnlyTools, UnsupportedMarket
from telegram_bot.handlers import telegram_html


def settings(tmp_path, **changes):
    base = dict(telegram_bot_token='123456:TRADING_AGENT_TEST_TOKEN', allowed_user_ids=frozenset({42}),
                allowed_chat_ids=frozenset(), state_path=tmp_path/'sessions', log_path=tmp_path/'agent.log',
                aws_region='selected-region', bedrock_model_id='configured-model',
                bedrock_endpoint_url='https://bedrock.invalid', hermes_agent_path=None,
                xai_api_key='test-key', xai_api_base_url='https://news.invalid', grok_model='configured-news-model',
                grok_timeout_seconds=0.1, max_history_messages=40, max_tool_iterations=4,
                api_token='test-internal-token')
    return TradingAgentSettings(**(base | changes))


def payload(data):
    return {'schema_version': 'agent-v1', 'data': data, 'observed_at': datetime.now(UTC).isoformat(), 'source': 'canonical-test-api'}


def transport(handler):
    async def process(request):
        assert request.method == 'GET'
        assert request.url.host == '127.0.0.1'
        assert request.headers['x-agent-token'] == 'test-internal-token'
        return httpx.Response(200, json=payload(await handler(request)))
    return httpx.MockTransport(process)


def api_for(handler, **kwargs):
    return SwiftChartAPI('http://127.0.0.1:8000', 'test-internal-token', transport=transport(handler), **kwargs)


def run(coroutine):
    return asyncio.run(coroutine)


@pytest.mark.parametrize('url', ['https://api.example.com', 'http://example.com', 'http://169.254.169.254', 'http://127.0.0.1@evil.invalid', 'http://127.0.0.1/api', 'http://127.0.0.1?x=1'])
def test_external_or_credentialled_origins_rejected(url):
    with pytest.raises(ValueError):
        SwiftChartAPI(url, 'test-token')


def test_singleflight_cache_and_copy_isolation():
    async def check():
        calls = []
        async def serve(request):
            calls.append(request.url.path)
            await asyncio.sleep(0.01)
            return {'current_price': 123.456}
        api = api_for(serve)
        results = await asyncio.gather(*(api.get('/snapshot/SOLUSDT') for _ in range(100)))
        assert len(calls) == 1
        results[0]['current_price'] = -999
        assert (await api.get('/snapshot/SOLUSDT'))['current_price'] == 123.456
        await api.close()
    run(check())


def test_http_concurrency_is_bounded_even_with_distinct_questions():
    async def check():
        active = maximum = 0
        async def serve(request):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            await asyncio.sleep(0.002)
            active -= 1
            return {'current_price': 5}
        api = api_for(serve)
        await asyncio.gather(*(api.get('/snapshot/COIN' + str(i)) for i in range(30)))
        assert maximum == 2
        await api.close()
    run(check())


def test_nested_performance_cannot_cross_the_client_boundary():
    async def check():
        async def serve(request):
            return {'status': 'TP1_HIT', 'take_profit_1': 130, 'take_profit_2': 140, 'pnl_r': 4, 'win_rate': 99,
                    'nested': {'total_trades': 100, 'results': [1], 'entry_price': 123}}
        api = api_for(serve)
        data = await api.get('/signals/crypto/7')
        assert data['status'] == 'TP1_HIT'
        assert data['take_profit_1'] == 130 and data['take_profit_2'] == 140
        assert data['nested'] == {'entry_price': 123}
        assert 'pnl_r' not in data and 'win_rate' not in data
        await api.close()
    run(check())


def test_redirect_is_not_followed_and_raw_error_is_not_exposed():
    async def check():
        def redirect(request):
            return httpx.Response(302, headers={'Location': 'http://untrusted.invalid'})
        api = SwiftChartAPI('http://127.0.0.1', 'test-token', transport=httpx.MockTransport(redirect))
        with pytest.raises(APIUnavailable, match='temporarily unavailable'):
            await api.get('/markets')
        await api.close()
    run(check())


@pytest.mark.parametrize('symbol', ['SOL', 'DOGE', 'XRP', 'BNB', 'LINK', 'AVAX', 'ADA', 'SUI', 'PEPE', 'NEAR'])
def test_dynamic_market_list_replaces_the_old_crypto_allowlist(tmp_path, symbol):
    async def check():
        async def serve(request):
            return {'crypto': [{'symbol': symbol+'USDT', 'base_asset': symbol}], 'forex': []}
        api = api_for(serve)
        tools = SwiftChartReadOnlyTools(api=api, settings=settings(tmp_path))
        assert await tools.resolve(symbol) == ('crypto', symbol+'USDT')
        with pytest.raises(UnsupportedMarket, match=symbol+'USDT'):
            await tools.resolve('NOT_LISTED')
        await api.close()
    run(check())


def test_session_files_are_private_isolated_and_bounded(tmp_path):
    store = ConversationStore(tmp_path/'sessions', max_messages=4)
    store.replace_history('chat:1:user:42', [{'role': 'user', 'content': str(i)} for i in range(8)])
    assert [m['content'] for m in store.get_history('chat:1:user:42')] == ['4','5','6','7']
    assert store.get_history('chat:2:user:42') == []
    assert store.get_history('chat:1:user:43') == []
    files = list((tmp_path/'sessions').iterdir())
    assert len(files) == 1 and files[0].suffix == '.json'
    assert files[0].stat().st_mode & 0o777 == 0o600
    store.reset('chat:1:user:42')
    assert store.get_history('chat:1:user:42') == []


def test_allowlist_semantics_are_preserved_and_empty_is_denied(tmp_path):
    selected = settings(tmp_path)
    assert selected.is_authorized(user_id=42, chat_id=99)
    assert not selected.is_authorized(user_id=43, chat_id=99)
    assert replace(selected, allowed_chat_ids=frozenset({99})).is_authorized(user_id=43, chat_id=99)
    assert not replace(selected, allowed_user_ids=frozenset()).is_authorized(user_id=42, chat_id=99)


@pytest.mark.parametrize('message', ['win rate', 'show performance', 'total trades', 'R results', 'profit', 'PnL', 'how many wins'])
def test_performance_questions_never_invoke_tools_or_models(tmp_path, message):
    class Never:
        def __getattr__(self, name):
            raise AssertionError('No external dependency should be used')
    service = TradingAgentService(settings(tmp_path), tools=Never(), orchestrator=Never(), grok=Never())
    answer = run(service.handle_message(chat_id=1, user_id=42, text=message))
    assert 'unavailable' in answer


def test_denied_user_does_not_touch_api_or_models(tmp_path):
    class Never:
        def __getattr__(self, name):
            raise AssertionError('Unauthorized request escaped access control')
    service = TradingAgentService(settings(tmp_path), tools=Never(), orchestrator=Never(), grok=Never())
    assert run(service.handle_message(chat_id=1, user_id=99, text='BTC price')) == 'This trading-agent bot is private.'


def test_100_parallel_answers_preserve_api_facts_and_do_not_invoke_models(tmp_path):
    async def check():
        calls = []
        async def serve(request):
            calls.append(request.url.path)
            if request.url.path.endswith('/markets'):
                return {'crypto': [{'symbol':'SOLUSDT', 'base_asset':'SOL'}], 'forex':[]}
            return {'symbol':'SOLUSDT', 'current_price':123.456789, 'setup_status':'NO_PUBLISHED_SIGNAL',
                    'support_zones':[{'lower':120.111,'upper':121.222}], 'signals':[]}
        api = api_for(serve)
        tools = SwiftChartReadOnlyTools(api=api, settings=settings(tmp_path))
        class Never:
            def __getattr__(self, name):
                raise AssertionError('Factual fast path must not request a model or Grok')
        service = TradingAgentService(settings(tmp_path), tools=tools, orchestrator=Never(), grok=Never())
        answers = await asyncio.gather(*(service.handle_message(chat_id=i, user_id=42, text='SOL levels') for i in range(100)))
        assert all('123.456789' in a and '120.111–121.222' in a and 'NO_PUBLISHED_SIGNAL' in a for a in answers)
        assert len(calls) == 2
        await api.close()
    run(check())


@pytest.mark.parametrize('message,expected', [('current BTC price',False), ('latest SOL levels',False), ('news about ETH',True), ('macro events',True)])
def test_grok_is_news_only(message, expected):
    assert needs_current_context(message) is expected


def test_hermes_remains_bedrock_only_with_no_session_database(tmp_path):
    class Registry:
        def __init__(self): self.names=[]
        def register(self, **kwargs): self.names.append(kwargs['name'])
    class API:
        async def get(self,*args,**kwargs): return {}
    selected = settings(tmp_path)
    registry=Registry()
    tools=SwiftChartReadOnlyTools(api=API(),settings=selected)
    orchestrator=HermesOrchestrator(selected,tools=tools,registry=registry,agent_factory=lambda **kwargs: None)
    names=orchestrator.register_tools()
    assert len(names)==8 and set(names)==set(registry.names)
    assert 'calculate_risk' not in names and 'get_trade_history' not in names
    kwargs=orchestrator._agent_kwargs(session_key='test')
    assert kwargs['provider']=='bedrock' and kwargs['api_mode']=='bedrock_converse'
    assert kwargs['enabled_toolsets']==['swiftchart_readonly']
    assert kwargs['max_tokens']==512 and kwargs['session_db'] is None
    assert kwargs['skip_memory'] and not kwargs['checkpoints_enabled']


def test_no_backend_exchange_or_database_imports_in_the_agent():
    for path in Path(__file__).parents[1].rglob('*.py'):
        if 'tests' in path.parts:
            continue
        tree=ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module=node.module or ''
                assert module.split('.')[0] not in {'app','backend','sqlite3','pandas','numpy','hyperliquid'}, (path,module)
            if isinstance(node,ast.Import):
                assert not {n.name.split('.')[0] for n in node.names} & {'app','backend','sqlite3','pandas','numpy','hyperliquid'}


def test_telegram_output_escapes_html_and_preserves_supported_formatting():
    assert telegram_html('**Status** <script>') == '<b>Status</b> &lt;script&gt;'


def test_client_cache_does_not_extend_server_response_age():
    async def check():
        calls=[]
        async def serve(request):
            calls.append(1)
            value=payload({'current_price':123})
            value['observed_at']=(datetime.now(UTC)-timedelta(seconds=2)).isoformat()
            return httpx.Response(200,json=value)
        api=SwiftChartAPI('http://127.0.0.1','test-token',ttl=1,transport=httpx.MockTransport(serve))
        await api.get('/snapshot/SOL')
        await api.get('/snapshot/SOL')
        assert len(calls)==2
        await api.close()
    run(check())


def test_take_profit_question_is_not_an_aggregate_performance_request():
    from trading_agent.response_context import performance_request
    assert not performance_request('Where is the take profit for SOL?')
    assert performance_request('What is the strategy profit?')


def test_detailed_analysis_uses_hermes_with_api_context_but_never_grok_or_model_levels(tmp_path):
    async def check():
        async def serve(request):
            if request.url.path.endswith('/markets'):
                return {'crypto':[{'symbol':'SOLUSDT','base_asset':'SOL'}],'forex':[]}
            return {'symbol':'SOLUSDT','current_price':123.4,'support_zones':[{'lower':120,'upper':121}],'setup_status':'NO_PUBLISHED_SIGNAL','signals':[]}
        api=api_for(serve)
        tools=SwiftChartReadOnlyTools(api=api,settings=settings(tmp_path))
        class Model:
            async def respond(self,**kwargs):
                assert '123.4' in kwargs['smart_context']
                return {'final_response':'Use entry 999 and target 1000.'}
        class Never:
            async def fetch(self,text):raise AssertionError('Not a news question')
        service=TradingAgentService(settings(tmp_path),tools=tools,orchestrator=Model(),grok=Never())
        answer=await service.handle_message(chat_id=1,user_id=42,text='Full analysis of SOL')
        assert '123.4' in answer and '999' not in answer and '1000' not in answer
        await api.close()
    run(check())


def test_standalone_bot_does_not_replace_the_signal_bot_token_or_handlers(monkeypatch,tmp_path):
    import bot.main as alert_bot
    from telegram.ext import MessageHandler
    from telegram_bot.main import build_application
    from telegram_bot.handlers import handle_text
    monkeypatch.setattr(alert_bot,'init_db',lambda:None)
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN','654321:EXISTING_ALERT_TEST_TOKEN')
    alert=alert_bot.build_application()
    selected=settings(tmp_path)
    agent=build_application(settings=selected,service=SimpleNamespace())
    assert alert.bot.token=='654321:EXISTING_ALERT_TEST_TOKEN'
    assert agent.bot.token==selected.telegram_bot_token
    assert any(handler.callback is handle_text for group in agent.handlers.values() for handler in group)
    assert not any(isinstance(handler,MessageHandler) for group in alert.handlers.values() for handler in group)


def test_grok_news_uses_x_and_web_but_price_questions_do_not_call_it(tmp_path):
    requests=[]
    async def handle(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200,json={'output_text':'Qualitative news context.'})
    def factory(**kwargs):return httpx.AsyncClient(transport=httpx.MockTransport(handle),**kwargs)
    client=GrokContextClient(settings(tmp_path),client_factory=factory)
    assert run(client.fetch('current BTC price')) is None
    assert not requests
    context=run(client.fetch('news about BTC'))
    assert context.text=='Qualitative news context.'
    assert requests[0]['tools']==[{'type':'x_search'},{'type':'web_search'}]


def test_grok_timeout_is_bounded_and_does_not_raise(tmp_path):
    calls=[]
    async def handle(request):
        calls.append(1)
        raise httpx.ReadTimeout('synthetic timeout')
    def factory(**kwargs):return httpx.AsyncClient(transport=httpx.MockTransport(handle),**kwargs)
    client=GrokContextClient(settings(tmp_path),client_factory=factory)
    assert run(client.fetch('latest news')) is None
    assert len(calls)==2


def test_database_guard_rejects_real_sqlite_including_memory(tmp_path):
    import os
    import subprocess
    import sys
    script = """
from trading_agent.hermes import enforce_no_database
enforce_no_database()
enforce_no_database()
import sqlite3
from pathlib import Path
for target in (':memory:', 'forbidden.db'):
    try:
        sqlite3.connect(target)
    except PermissionError:
        pass
    else:
        raise AssertionError('SQLite unexpectedly permitted')
assert not Path('forbidden.db').exists()
"""
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).parents[1]))
    subprocess.run([sys.executable, '-c', script], cwd=tmp_path, env=env, check=True)
