"""Read-only capture and 20-symbol API-to-agent/website projection parity.

Predeployment checks replay captured production responses through the candidate
internal router. They never invoke the production GET /analyze (which persists
ideas), a scanner refresh, a Telegram operation, or a trading write.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'backend'),str(ROOT/'agent'),str(ROOT)]

import httpx
from fastapi import FastAPI
from app.routes.agent_internal import router
from app.services import agent_data
from app.forex.models import ForexSignalPlan
from trading_agent.api import SwiftChartAPI
from trading_agent.config import TradingAgentSettings
from trading_agent.tools import SwiftChartReadOnlyTools


async def capture(origin):
    async with httpx.AsyncClient(base_url=origin,timeout=60,trust_env=False,follow_redirects=False) as client:
        async def read(path,**params):
            response=await client.get(path,params=params)
            response.raise_for_status()
            await asyncio.sleep(0.15)
            return response.json()
        catalog=await read('/api/markets',exchange='hyperliquid')
        available={row['symbol'] for row in catalog}
        preferred=['BTCUSDT','ETHUSDT','SOLUSDT','XRPUSDT','DOGEUSDT','BNBUSDT','HYPEUSDT','ADAUSDT','LINKUSDT','AVAXUSDT']
        crypto=[]
        for symbol in [*preferred,*sorted(available-set(preferred))]:
            if symbol not in available:
                continue
            page=await read('/api/trade-history',symbol=symbol,limit=1)
            if page.get('records'):
                row=await read('/api/trade-history/'+str(page['records'][0]['id']))
                crypto.append(row)
            if len(crypto)==10:
                break
        forex_signals=(await read('/api/forex/signals',limit=500)).get('signals',[])
        pairs=await read('/api/forex/pairs')
        symbols=[row.get('symbol') or row['pair'] for row in pairs['pairs']][:10]
        forex=[]
        higher=[]
        for symbol in symbols:
            forex.append(await read('/api/forex/pairs/'+symbol+'/chart',timeframe='1H',limit=240))
            higher.append(await read('/api/forex/pairs/'+symbol+'/chart',timeframe='4H',limit=240))
    if len(crypto)!=10 or len(forex)!=10:
        raise ValueError('Require ten crypto signal records and ten Forex chart snapshots')
    return {'captured_at':datetime.now(UTC).isoformat(),'origin':origin,'crypto':crypto,'forex':forex,'forex_higher':higher,'forex_signals':forex_signals}


def website_status(module,function,value):
    script='''import {pathToFileURL} from 'node:url';
const mod=await import(pathToFileURL(process.argv[1]).href);
let data='';for await(const chunk of process.stdin)data+=chunk;
console.log(JSON.stringify(mod[process.argv[2]](JSON.parse(data))));'''
    return json.loads(subprocess.check_output(['node','--input-type=module','-e',script,str(ROOT/module),function],input=json.dumps(value),text=True))


async def replay(data):
    token=secrets.token_urlsafe(32)
    os.environ['AGENT_INTERNAL_API_TOKEN']=token
    app=FastAPI();app.include_router(router)
    api=SwiftChartAPI('http://127.0.0.1',token,transport=httpx.ASGITransport(app=app,client=('127.0.0.1',1234)))
    tools=SwiftChartReadOnlyTools(api=api,settings=TradingAgentSettings.from_env())
    catalog={'crypto':[{'symbol':row['symbol'],'base_asset':row['symbol'].removesuffix('USDT')} for row in data['crypto']],
             'forex':[{'symbol':row['symbol'],'base_asset':row['symbol']} for row in data['forex']]}
    crypto={str(row['id']):row for row in data['crypto']}
    forex={row['symbol']:row for row in data['forex']}
    async def markets():return agent_data.envelope(catalog,'captured production market list')
    def signal(identity):return crypto.get(str(identity))
    def forex_signals(*args,**kwargs):return [ForexSignalPlan(**row) for row in data.get('forex_signals',[])]
    async def chart(symbol,**kwargs):return forex[symbol]
    results=[]
    with patch.object(agent_data,'markets',markets), patch.object(agent_data,'get_trade_history',signal), patch.object(agent_data,'forex_pair_chart',chart), patch.object(agent_data,'list_signals',forex_signals):
        for row in data['crypto']:
            answer=await tools.get_signal({'signal_id':str(row['id']),'market_type':'crypto'})
            keys=['symbol','timeframe','id','entry_price','entry_zone_low','entry_zone_high','stop_loss','take_profit_1','take_profit_2','tracking_deadline','status','lifecycle_status']
            mismatches=[key for key in keys if key in row and answer.get(key)!=row[key]]
            website=website_status('frontend/src/lib/cryptoLifecycle.js','cryptoLifecycleStatus',row)
            if answer.get('display_status')!=website:mismatches.append('website_display_status')
            results.append({'market':'crypto','symbol':row['symbol'],'signal_id':row['id'],'fields_checked':keys+['website_display_status'],'mismatches':mismatches})
        for row in data['forex']:
            answer=await tools.get_market_snapshot({'symbol':row['symbol'],'market_type':'forex','timeframe':'1H'})
            keys=['symbol','timeframe','current_price','decision','levels','bias','htf_context']
            mismatches=[key for key in keys if answer.get(key)!=row.get(key)]
            for zone_key in ('buy_zones','sell_zones'):
                expected=agent_data.zone_projection(row.get(zone_key,[]))
                if answer.get(zone_key)!=expected:mismatches.append(zone_key)
            ui_source=website_status('frontend/src/lib/forexPairState.js','forexZoneStatus',row)
            ui_agent=website_status('frontend/src/lib/forexPairState.js','forexZoneStatus',answer)
            if ui_source!=ui_agent:mismatches.append('website_display_status')
            results.append({'market':'forex','symbol':row['symbol'],'fields_checked':keys+['buy_zones','sell_zones','website_display_status'],'mismatches':mismatches})
    await api.close()
    return {'passed':all(not item['mismatches'] for item in results),'symbols':results,
            'captured_at':data['captured_at'],'origin':data['origin'],
            'mode':'captured-production-response replay through candidate internal HTTP router and actual website status helpers',
            'live_internal_routes_deployed':False}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--origin',default='https://swiftchart.xyz')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--input',type=Path)
    args=parser.parse_args()
    os.umask(0o077)
    args.output.mkdir(parents=True,exist_ok=False,mode=0o700)
    data=json.loads(args.input.read_text()) if args.input else asyncio.run(capture(args.origin))
    (args.output/'production-responses.private.json').write_text(json.dumps(data))
    report=asyncio.run(replay(data))
    (args.output/'report.json').write_text(json.dumps(report,indent=2))
    print(json.dumps({'passed':report['passed'],'symbols':len(report['symbols']),'mismatched_symbols':[r['symbol'] for r in report['symbols'] if r['mismatches']],'mode':report['mode']}))
    raise SystemExit(0 if report['passed'] else 1)
