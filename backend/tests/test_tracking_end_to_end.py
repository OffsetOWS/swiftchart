import asyncio
import json
from types import SimpleNamespace
import pandas as pd
import pytest

from app.models.schemas import TradeHistoryPage, TradeStats
from app.services import trade_history, tracking_funding
from app.services.tracking_ledger import ensure_tracking_ledger
from bot.formatter import format_stats
from test_tracking_corrections import START, bars
from test_tracking_delivery import configure_db
from test_tracking_funding import rates


def test_replay_persists_costs_and_api_and_bot_use_the_same_sample(monkeypatch, tmp_path):
    db = configure_db(monkeypatch, tmp_path)
    with db.get_connection() as c:
        c.execute("""INSERT INTO trade_ideas
            (id,symbol,timeframe,exchange,direction,entry_zone_low,entry_zone_high,stop_loss,
             take_profit_1,take_profit_2,risk_reward,confidence,reason,invalidation,created_at)
            VALUES (1,'BTCUSDT','4h','hyperliquid','LONG',99,101,90,110,120,2,80,'reason','stop',?)""",
            (START.isoformat(),))
        c.execute("INSERT INTO crypto_delivery_receipts VALUES (1,'chat',1,?)", (START.isoformat(),))
        raw = dict(c.execute("SELECT * FROM trade_ideas").fetchone())
    async def candles(*args):
        return pd.DataFrame(bars((100,102,99,101),(105,111,101,110),(115,121,111,120)))
    async def funding(*args): return rates()
    monkeypatch.setattr(trade_history, 'get_candles_cached', candles)
    monkeypatch.setattr(tracking_funding, 'fetch_rates', funding)
    assert asyncio.run(trade_history._evaluate_rows([raw]))['funding_unknown'] == 0
    page = TradeHistoryPage.model_validate(trade_history.query_trade_history({}))
    record = page.records[0]
    assert record.gross_r_multiple == 1.5
    assert record.net_r_multiple == pytest.approx(1.5-.01075-.1)
    stats = TradeStats.model_validate(trade_history.stats())
    assert stats.expectancy_sample_count == 1
    assert stats.expectancy_per_trade == 1.39
    summary = format_stats(stats.model_dump())
    assert 'Costed sample: 1' in summary
    assert 'Win rate' not in summary
    # The final correction includes raw rates so the estimate can be reproduced.
    with db.get_connection() as c:
        evidence = json.loads(c.execute('SELECT evidence_json FROM tracking_corrections ORDER BY id DESC LIMIT 1').fetchone()[0])
        assert evidence['costs']['rates'] == rates()


def test_legacy_rows_have_no_certified_cost_fields_before_replay(monkeypatch, tmp_path):
    db = configure_db(monkeypatch, tmp_path)
    with db.get_connection() as c:
        c.execute("""INSERT INTO trade_ideas
            (id,symbol,timeframe,exchange,direction,entry_zone_low,entry_zone_high,stop_loss,
             take_profit_1,take_profit_2,risk_reward,confidence,reason,invalidation,status,result,pnl_r_multiple)
            VALUES (1,'BTCUSDT','4h','hyperliquid','LONG',99,101,90,110,120,2,80,'reason','stop','TP2_HIT','WIN',2)""")
    record = trade_history.get_trade_history(1)
    assert record['net_r_multiple'] is None
    assert record['gross_r_multiple'] is None
    assert record['cost_status'] == 'UNKNOWN'
    assert trade_history.stats()['legacy_unreplayed_count'] == 1
