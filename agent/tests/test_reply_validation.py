import asyncio

from trading_agent.response_context import checked_reply, render_snapshot
from trading_agent.service import TradingAgentService
from test_agent_api import display_facts, settings


def test_hallucinated_number_falls_back_to_api_template():
    data={'reply':display_facts()}
    candidate=render_snapshot(data).replace('123.456789','99999')
    answer,accepted,reason=checked_reply(candidate,data)
    assert not accepted and reason=='ungrounded_number'
    assert '99999' not in answer and answer==render_snapshot(data)


def test_correct_numbers_in_wrong_role_or_wrong_status_also_fail():
    data={'reply':display_facts()}
    for candidate in [render_snapshot(data).replace('Support:','Resistance:'),
                      render_snapshot(data).replace('no active signal','signal active')]:
        answer,accepted,reason=checked_reply(candidate,data)
        assert not accepted and reason=='shape_or_fact_mismatch' and answer==render_snapshot(data)


def test_model_outage_uses_same_grounded_five_line_template(tmp_path):
    class Model:
        async def respond(self,**kwargs):raise RuntimeError('offline')
    service=TradingAgentService(settings(tmp_path),orchestrator=Model())
    data={'reply':display_facts()}
    assert asyncio.run(service._write_snapshot(data))==render_snapshot(data)


def test_missing_api_display_contract_cannot_leak_raw_codes_or_unrounded_prices():
    answer=render_snapshot({'symbol':'SOLUSDT','current_price':123.456789,'decision':'NO_TRADE'})
    assert '123.456789' not in answer and 'NO_TRADE' not in answer
