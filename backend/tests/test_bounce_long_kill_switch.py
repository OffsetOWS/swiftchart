from app.config import get_settings
from app.services.trade_history import bounce_long_kill_switch_tripped
from app.utils import database


def test_kill_switch_latches_after_fifteen_negative_completed_trades(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'bounce-kill.db'}")
    get_settings.cache_clear()
    database._INITIALIZED = False
    database.init_db()
    with database.get_connection() as connection:
        for i in range(14):
            connection.execute(
                """INSERT INTO trade_ideas(symbol,timeframe,exchange,direction,entry_zone_low,entry_zone_high,
                   stop_loss,take_profit_1,take_profit_2,risk_reward,confidence,reason,invalidation,
                   setup_family,status,result,pnl_r_multiple,closed_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)""",
                (f"C{i}USDT", "1h", "hyperliquid", "LONG", 10, 10, 9, 12, 12, 2, 80,
                 "test", "below stop", "bounce_long", "SL_HIT", "LOSS", -1.0),
            )
    assert not bounce_long_kill_switch_tripped()
    with database.get_connection() as connection:
        connection.execute(
            """INSERT INTO trade_ideas(symbol,timeframe,exchange,direction,entry_zone_low,entry_zone_high,
               stop_loss,take_profit_1,take_profit_2,risk_reward,confidence,reason,invalidation,
               setup_family,status,result,pnl_r_multiple,closed_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)""",
            ("C14USDT", "1h", "hyperliquid", "LONG", 10, 10, 9, 12, 12, 2, 80,
             "test", "below stop", "bounce_long", "SL_HIT", "LOSS", -1.0),
        )
    assert bounce_long_kill_switch_tripped()
    with database.get_connection() as connection:
        connection.execute("UPDATE trade_ideas SET pnl_r_multiple=10 WHERE setup_family='bounce_long'")
    assert bounce_long_kill_switch_tripped()  # Persisted latch cannot reactivate later.
    database._INITIALIZED = False
    get_settings.cache_clear()
