from app.config import get_settings
from app.services.signal_limits import cap_signals_per_scan
from test_canonical_telegram_dispatch import idea


def test_limits_preserve_rank_order_without_inventing_a_directional_quota(monkeypatch):
    monkeypatch.setenv("CRYPTO_MAX_SIGNALS_PER_SCAN","3")
    monkeypatch.setenv("CRYPTO_MAX_SIGNALS_PER_DIRECTION","2")
    get_settings.cache_clear()
    rows=[idea(symbol=f"C{i}USDT",direction=d) for i,d in enumerate(["Long","Long","Long","Short","Short"])]
    identities=[r.opportunity_key for r in rows]
    assert cap_signals_per_scan(rows)==[rows[0],rows[1],rows[3]]
    assert cap_signals_per_scan(rows[:3])==rows[:2]
    assert [r.opportunity_key for r in rows]==identities
    get_settings.cache_clear()


def test_limits_are_settings_and_accept_an_empty_scan(monkeypatch):
    monkeypatch.setenv("CRYPTO_MAX_SIGNALS_PER_SCAN","1")
    monkeypatch.setenv("CRYPTO_MAX_SIGNALS_PER_DIRECTION","1")
    get_settings.cache_clear()
    rows=[idea(symbol="BTCUSDT"),idea(symbol="ETHUSDT",direction="Short")]
    assert cap_signals_per_scan(rows)==rows[:1]
    assert cap_signals_per_scan([])==[]
    get_settings.cache_clear()


def test_production_cap_keeps_shadow_and_pending_retest_cohorts(monkeypatch):
    from app.services.signal_limits import cap_production_cohorts
    monkeypatch.setenv("CRYPTO_MAX_SIGNALS_PER_SCAN","1")
    get_settings.cache_clear()
    first=idea();second=idea(symbol="ETHUSDT")
    shadow=idea(symbol="SOLUSDT");shadow.strategy_decision="SHADOW"
    pending=idea(symbol="AVAXUSDT",entry_status="WAIT_FOR_RETEST")
    rows=[first,shadow,second,pending]
    assert cap_production_cohorts(rows)==[first,shadow,pending]
    get_settings.cache_clear()
