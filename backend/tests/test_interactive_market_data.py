import asyncio
import builtins
import fcntl
from datetime import UTC, datetime
import threading
from types import SimpleNamespace

import pandas as pd

from app.services import market_data, scanner
from app.services.agent_presentation import chart_projection, presentation


def test_unverified_variational_is_excluded_even_when_requested(monkeypatch):
    monkeypatch.setattr(scanner,'get_settings',lambda:SimpleNamespace(variational_enabled=True,variational_candles_verified=False))
    assert scanner.selected_exchanges('all')==['hyperliquid']
    assert scanner.selected_exchanges('variational')==[]
    monkeypatch.setattr(scanner,'get_settings',lambda:SimpleNamespace(variational_enabled=True,variational_candles_verified=True))
    assert scanner.selected_exchanges('all')==['hyperliquid','variational']


def test_mixed_case_provider_identifier_is_preserved(monkeypatch):
    seen=[]
    async def catalogue(exchange):return [{'symbol':'KPEPEUSDT','exchange_symbol':'kPEPEUSDT'}]
    class Exchange:
        async def get_candles(self,symbol,timeframe,limit):
            seen.append(symbol)
            return pd.DataFrame([{'timestamp':datetime.now(UTC),'close':1}])
    monkeypatch.setattr(market_data,'get_markets_cached',catalogue)
    monkeypatch.setattr(market_data,'get_exchange',lambda exchange:Exchange())
    monkeypatch.setattr(market_data,'get_settings',lambda:SimpleNamespace(hyperliquid_candle_fetch_attempts=1,hyperliquid_candle_fetch_backoff_seconds=0,hyperliquid_candle_concurrency=1,hyperliquid_candle_request_delay_seconds=0))
    monkeypatch.setattr(market_data,'_candle_semaphores',{})
    asyncio.run(market_data._fetch_candles_with_retries('hyperliquid','KPEPEUSDT','4h',100))
    assert seen==['kPEPEUSDT']


def test_pacing_file_contention_does_not_block_event_loop(tmp_path,monkeypatch):
    path=tmp_path/'pace';original=builtins.open
    monkeypatch.setattr(market_data,'open',lambda *args,**kwargs:original(path,*args[1:],**kwargs),raising=False)
    monkeypatch.setattr(market_data,'get_settings',lambda:SimpleNamespace(hyperliquid_candle_request_delay_seconds=.001))
    lock=original(path,'a+');fcntl.flock(lock,fcntl.LOCK_EX)
    timer=threading.Timer(.15,lambda:fcntl.flock(lock,fcntl.LOCK_UN));timer.start()
    async def run():
        task=asyncio.create_task(market_data._pace_candle_request('hyperliquid'))
        beats=0
        while not task.done():
            await asyncio.sleep(.01);beats+=1
        await task
        assert beats>=8
    try:asyncio.run(run())
    finally:timer.join();lock.close()


def test_interactive_queue_priority_fairness_and_cancellation():
    async def run():
        slots=market_data.CandleSlots(1);order=[];release=asyncio.Event()
        async def work(name,interactive=False):
            if interactive:
                with market_data.interactive_candles():
                    async with slots:order.append(name);await asyncio.sleep(0)
            else:
                async with slots:
                    order.append(name)
                    if name=='holder':await release.wait()
        holder=asyncio.create_task(work('holder'));await asyncio.sleep(0)
        background=asyncio.create_task(work('background'));await asyncio.sleep(0)
        cancelled=asyncio.create_task(work('cancelled',True));await asyncio.sleep(0);cancelled.cancel()
        await asyncio.gather(cancelled,return_exceptions=True)
        tasks=[asyncio.create_task(work(str(i),True)) for i in range(5)]
        await asyncio.sleep(0);release.set()
        await asyncio.gather(holder,background,*tasks)
        assert order[1:4]==['0','1','2'] and order[4]=='background'
        assert slots.used==0 and not slots.waiters and 'cancelled' not in order
    asyncio.run(run())


def test_no_trade_chart_has_no_candidate_execution_prices_and_pending_forex_is_active():
    now=datetime.now(UTC).isoformat()
    data={'symbol':'EURUSD','timeframe':'1H','market_type':'forex','current_price':1.12,
          'decision':'NO_TRADE','levels':{'entry':999,'stop':888,'target':777},
          'chart_candles':[{'timestamp':now,'open':1.12,'high':1.13,'low':1.11,'close':1.12} for _ in range(100)]}
    result=chart_projection(data,observed_at=now)
    assert [row['label'] for row in result['levels']]==['Current']
    data['signals']=[{'status':'PENDING_ENTRY'}]
    assert presentation(data,observed_at=now)['signal']=='signal active, awaiting entry'
