"""Explicit deployment probes only. Never start a Telegram poller or send a message."""
import argparse
import asyncio
import json
import logging

from trading_agent.config import TradingAgentSettings
from trading_agent.hermes import HermesOrchestrator
from trading_agent.tools import SwiftChartReadOnlyTools


async def check(args):
    selected=TradingAgentSettings.from_env()
    selected.validate_for_bot()
    tools=SwiftChartReadOnlyTools(settings=selected)
    result={}
    if args.api:
        catalog=await tools.get_markets()
        result['api']=bool(catalog.get('crypto') and catalog.get('forex'))
    if args.telegram:
        from telegram import Bot
        async with Bot(selected.telegram_bot_token) as bot:
            identity=await bot.get_me()
            result['telegram']=identity.username==args.expected_username
    if args.bedrock:
        orchestrator=HermesOrchestrator(selected,tools=tools)
        response=await asyncio.wait_for(orchestrator.respond(
            message='Reply with the word Ready. Do not call any tool.',history=[],session_key='deployment-readiness-probe'),
            timeout=selected.hermes_timeout_seconds)
        result['bedrock']=bool(response.get('final_response'))
    await tools.api.close()
    print(json.dumps(result))
    return 0 if result and all(result.values()) else 1


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--api',action='store_true')
    parser.add_argument('--telegram',action='store_true')
    parser.add_argument('--bedrock',action='store_true')
    parser.add_argument('--expected-username',default='swift_agent_bot')
    args=parser.parse_args()
    logging.disable(logging.CRITICAL)
    try:
        raise SystemExit(asyncio.run(check(args)))
    except Exception as exc:
        print(json.dumps({'probe_failed':True,'error_type':type(exc).__name__}))
        raise SystemExit(1)
