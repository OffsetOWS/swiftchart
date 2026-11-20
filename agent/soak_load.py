"""100 concurrent real service turns against the isolated release-soak API.

No Telegram poller or model provider is started. The real service exercises its
API-grounded fallback because the offline gate blocks outbound provider access.
Separate provider checks are required to verify model composition.
"""
import asyncio
from dataclasses import replace
from pathlib import Path
import sys
from time import monotonic

sys.path.insert(0, str(Path(__file__).resolve().parent))

from trading_agent.api import SwiftChartAPI
from trading_agent.config import TradingAgentSettings
from trading_agent.service import TradingAgentService
from trading_agent.tools import SwiftChartReadOnlyTools


def run_load(origin, token, output, count=100):
    async def run():
        settings = replace(TradingAgentSettings.from_env(), api_base_url=origin, api_token=token,
                           allowed_user_ids=frozenset({1}), allowed_chat_ids=frozenset(),
                           state_path=Path(output)/'agent-sessions')
        api = SwiftChartAPI(origin, token, timeout=30)
        tools = SwiftChartReadOnlyTools(api=api, settings=settings)
        class NoProvider:
            async def respond(self, **kwargs):
                from trading_agent.hermes import HermesUnavailable
                raise HermesUnavailable('Provider access is disabled in the offline release gate')
            async def fetch(self, text):
                raise AssertionError('Factual questions must not request news')
        service = TradingAgentService(settings, tools=tools, orchestrator=NoProvider(), grok=NoProvider())
        catalog = await tools.get_markets()
        symbols = list(dict.fromkeys(row['symbol'] for row in catalog.get('crypto', [])))[:10]
        forex = [row['symbol'] for row in catalog.get('forex', [])][:10]
        if len(forex) < 10:
            raise ValueError('Load requires ten supported API Forex symbols')
        if len(symbols) < 10:
            raise ValueError('Load requires ten supported API crypto symbols')
        durations=[]
        async def question(index):
            selected = symbols + forex
            symbol = selected[index % len(selected)]
            started=monotonic()
            answer=await service.handle_message(chat_id=index+1,user_id=1,text=f'{symbol} levels')
            durations.append(monotonic()-started)
            return (symbol in answer and 'Data:' in answer and 'Price:' in answer
                    and 'Price: unavailable' not in answer and len(answer.splitlines()) == 5)
        started=monotonic()
        results=await asyncio.gather(*(question(index) for index in range(count)))
        await api.close()
        return {'requested':count,'concurrency_requested':count,'answered':sum(results),
                'failures':len(results)-sum(results),'passed':all(results),
                'elapsed_seconds':monotonic()-started,'worst_seconds':max(durations),
                'p95_seconds':sorted(durations)[int((len(durations)-1)*.95)],
                'crypto_symbols':symbols,'forex_symbols':forex,'provider_calls':0,'mode':'real-agent-api-grounded-fallback-provider-offline'}
    return asyncio.run(run())
