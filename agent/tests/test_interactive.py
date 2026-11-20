import asyncio
from datetime import UTC, datetime, timedelta
from io import BytesIO
import json
from types import SimpleNamespace

import httpx
import pytest
from PIL import Image

from app.services.agent_presentation import presentation, chart_projection
from app.services.agent_links import signal_agent_url
from trading_agent.alerts import AlertStore, deliver_alerts
from trading_agent.charts import CandleCharts
from trading_agent.service import TradingAgentService
from trading_agent.tools import SwiftChartReadOnlyTools
from telegram_bot.handlers import market_keyboard
from test_agent_api import settings, api_for


def snapshot(symbol='BTCUSDT', timeframe='4h', detail=False):
    now=datetime.now(UTC)
    data={'symbol':symbol,'timeframe':timeframe,'market_type':'crypto','current_price':63700,
          'price_format':{'kind':'hyperliquid-perp','size_decimals':5},'market_condition':'TRENDING_DOWN',
          'structure':'LH/LL','support_zones':[{'lower':62000,'upper':62500}],
          'resistance_zones':[{'lower':65000,'upper':66000}], 'signals':[],
          'chart_candles':[{'timestamp':(now-timedelta(hours=100-i)).isoformat(),'open':63600,'high':63800,'low':63500,'close':63700} for i in range(100)]}
    if detail:
        data['companion']={'reply':{'timeframe':'1D','trend':'downward','structure':'price is forming lower highs and lower lows'}}
    data['reply']=presentation(data,observed_at=now.isoformat())
    return data


class Model:
    async def respond(self, **kwargs):
        facts=json.loads(kwargs['smart_context'].split(': ',1)[1])
        return {'final_response':'\n'.join(row[-1] for row in facts['line_options'])}


def test_conversation_buttons_followups_deep_link_and_alert_once(tmp_path):
    async def run():
        paths=[]
        async def serve(request):
            path=request.url.path;paths.append(path)
            if path.endswith('/markets'):
                return {'crypto':[{'symbol':s+'USDT','base_asset':s} for s in ('BTC','ETH')], 'forex':[]}
            if '/prices' in path:
                return {'quotes':{s:{'price':'63700','display_price':'63700','observed_at':datetime.now(UTC).isoformat()} for s in request.url.params['symbols'].split(',')}}
            symbol=path.rsplit('/',1)[-1] if '/snapshot/' in path or '/chart/' in path else 'BTCUSDT'
            data=snapshot(symbol,request.url.params.get('timeframe','4h'),request.url.params.get('detail')=='true')
            return chart_projection(data,observed_at=datetime.now(UTC).isoformat()) if '/chart/' in path else data
        api=api_for(serve)
        service=TradingAgentService(settings(tmp_path),tools=SwiftChartReadOnlyTools(api=api,settings=settings(tmp_path)),orchestrator=Model())
        a=await service.handle_turn(chat_id=7,user_id=42,text='BTC 4h levels')
        assert a.model_written and len(a.text.splitlines())==4 and a.seconds<5
        token=a.context['token']
        buttons=[b for row in market_keyboard(a.context).inline_keyboard for b in row]
        assert [b.text for b in buttons]==['Chart','More detail','1H','4H','1D','News','Alert me']
        assert all(len(b.callback_data.encode())<=64 for b in buttons)
        detail=await service.interact(chat_id=7,user_id=42,token=token,action='detail')
        assert len(detail.text.splitlines())==9 and '1D context' in detail.text
        assert detail.context['token']==token and 'NO_TRADE' not in detail.text
        chart=await service.interact(chat_id=7,user_id=42,token=token,action='chart')
        image=Image.open(BytesIO(CandleCharts().render(chart.data)))
        assert image.size==(1280,780) and chart.data['candle_count']==100
        for tf in ('1h','4h','1d'):
            a=await service.interact(chat_id=7,user_id=42,token=token,action='tf'+tf)
            assert a.context['token']==token and a.context['timeframe']==tf
        a=await service.handle_turn(chat_id=7,user_id=42,text='what about ETH?')
        assert a.context['symbol']=='ETHUSDT' and a.context['timeframe']=='1d'
        a=await service.handle_turn(chat_id=7,user_id=42,text='and the daily?')
        assert a.context['symbol']=='ETHUSDT' and a.context['timeframe']=='1d'
        unsupported=await service.handle_turn(chat_id=7,user_id=42,text='what about foobar?')
        assert 'not covered' in unsupported.text and 'BTCUSDT' in unsupported.text
        await service.interact(chat_id=7,user_id=42,token=a.context['token'],action='alert')
        a=await service.handle_turn(chat_id=7,user_id=42,text='63,700')
        assert 'Alert set' in a.text
        sent=[]
        class Bot:
            async def send_message(self,**kwargs): sent.append(kwargs)
        assert await deliver_alerts(service,Bot())==1
        assert await deliver_alerts(service,Bot())==0 and len(sent)==1
        linked=await service.open_signal(7,42,'crypto','123')
        assert linked.context['signal_id']=='123' and '/internal/agent/signal-context/crypto/123' in paths
        with pytest.raises(ValueError):
            await service.interact(chat_id=7,user_id=99,token=token,action='chart')
        await api.close()
    asyncio.run(run())


def test_alert_limits_expiry_ownership_staleness_and_ambiguous_delivery(tmp_path):
    now=datetime.now(UTC);store=AlertStore(tmp_path/'alerts');market={'symbol':'BTCUSDT','market_type':'crypto','timeframe':'4h'}
    quote={'price':'100','display_price':'100','observed_at':now.isoformat()}
    with pytest.raises(ValueError):store.create(1,1,market,101,{**quote,'observed_at':(now-timedelta(minutes=2)).isoformat()},now=now)
    rows=[store.create(1,1,market,101,quote,now=now) for _ in range(10)]
    with pytest.raises(ValueError):store.create(1,1,market,101,quote,now=now)
    assert not store.cancel(2,rows[0]['id'])
    assert store.claim_hit(1,rows[0]['id'],quote,now=now) is None
    hit={**quote,'price':'102'}
    assert store.claim_hit(1,rows[0]['id'],hit,now=now)
    assert AlertStore(tmp_path/'alerts').claim_hit(1,rows[0]['id'],hit,now=now) is None
    store.delivered(1,rows[0]['id'],success=False)
    assert store.claim_hit(1,rows[0]['id'],hit,now=now) is None
    assert store.list(1,now=now+timedelta(days=7))==[]
    assert all(p.stat().st_mode & 0o077 == 0 for p in (tmp_path/'alerts').glob('*'))


def test_link_payload_is_canonical_and_bounded():
    assert signal_agent_url('crypto',123)=='https://t.me/swift_agent_bot?start=s_c_123'
    with pytest.raises(ValueError):signal_agent_url('forex','x'*61)
    with pytest.raises(ValueError):signal_agent_url('crypto','../token')
