from datetime import UTC, datetime
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

from app.models.schemas import Zone
from app.services.agent_presentation import nearest_zones, presentation, price_text
from app.strategy.support_resistance import find_support_resistance, nearest_range


@pytest.mark.parametrize('last_price', [50, 150])
def test_sudden_break_does_not_leave_historical_zones_on_the_wrong_side(last_price):
    prices = 100 + np.sin(np.arange(120) * 0.8) * 5
    prices[-1] = last_price
    frame = pd.DataFrame({'timestamp':pd.date_range('2026-01-01', periods=120, freq='4h', tz='UTC'),
                          'open':prices,'close':prices,'low':prices-1,'high':prices+1,'volume':100})
    supports, resistances = find_support_resistance(frame)
    assert all(zone.upper <= last_price for zone in supports)
    assert all(zone.lower >= last_price for zone in resistances)
    if last_price == 50:
        assert not supports and resistances
    else:
        assert supports and not resistances


def test_no_wrong_side_nearest_fallback_even_for_old_saved_zones():
    support = Zone(type='support', lower=79, upper=81, strength=.9, touches=5)
    resistance = Zone(type='resistance', lower=40, upper=42, strength=.9, touches=5)
    assert nearest_range(61, [support], [resistance]) == (None, None)


def test_a_zone_containing_price_is_a_touch_not_a_broken_zone():
    support = Zone(type='support', lower=59, upper=62, strength=.9, touches=5)
    resistance = Zone(type='resistance', lower=60, upper=63, strength=.9, touches=5)
    assert nearest_range(61, [support], [resistance]) == (support, resistance)
    data={'symbol':'BTCUSDT','timeframe':'4h','market_type':'crypto','current_price':61,
          'price_format':{'kind':'hyperliquid-perp','size_decimals':5},
          'support_zones':[support.model_dump()],'resistance_zones':[resistance.model_dump()]}
    facts=presentation(data,observed_at='2026-11-20T20:00:00Z')
    assert 'price inside zone' in facts['line_options'][2][0]


@pytest.mark.parametrize('symbol,value,expected', [('EURUSD',1.099246,'1.09925'),('USDJPY',153.12356,'153.124'),('XAUUSD',2641.235,'2641.24')])
def test_forex_display_precision(symbol,value,expected):
    assert price_text(value,'forex',symbol,{}) == expected


@pytest.mark.parametrize('value,size_decimals,expected', [(61381.123,5,'61381'),(2345.678,4,'2345.7'),(.01234567,2,'0.0123'),(.0001234,0,'0.000123')])
def test_crypto_uses_provider_tick_rules(value,size_decimals,expected):
    assert price_text(value,'crypto','COIN',{'kind':'hyperliquid-perp','size_decimals':size_decimals}) == expected


def test_unknown_crypto_tick_never_gets_guessed():
    assert price_text(1.23456,'crypto','COIN',{}) is None
    assert price_text(1.23456,'crypto','COIN',{'tick_size':'.25'}) == '1.25'


def test_forex_wrong_side_or_terminal_zone_is_omitted_not_role_flipped():
    original={'current_price':1.1,'buy_zones':[{'low':1.08,'high':1.09,'state':'CONFIRMED'}],
              'sell_zones':[{'low':1.0968,'high':1.0977,'state':'SWEPT'},
                            {'low':1.11,'high':1.12,'state':'INVALIDATED'},
                            {'low':1.13,'high':1.14,'state':'ACTIVE'}]}
    support,resistance=nearest_zones(original)
    assert support==(Decimal('1.08'),Decimal('1.09'))
    assert resistance==(Decimal('1.13'),Decimal('1.14'))
    assert original['sell_zones'][0]['state']=='SWEPT'


def test_no_trade_display_has_no_hypothetical_entry_stop_targets_or_codes():
    data={'symbol':'EURUSD','market_type':'forex','timeframe':'4H','current_price':1.09924,
          'decision':'NO_TRADE','bias':'BEARISH','htf_context':'HTF_NEUTRAL','regime':'BEARISH_PULLBACK',
          'levels':{'confirmation':999,'invalidation':888,'target_1':777},'signals':[],
          'buy_zones':[{'low':1.0955,'high':1.0965428,'state':'CONFIRMED'}],
          'sell_zones':[{'low':1.0968,'high':1.097735,'state':'SWEPT'}]}
    facts=presentation(data,observed_at=datetime(2026,11,20,20,tzinfo=UTC).isoformat())
    reply='\n'.join(row[0] for row in facts['line_options'])
    assert facts['signal']=='no active signal' and facts['resistance'] is None
    assert '1.09550–1.09654' in reply
    assert all(word not in reply for word in ['NO_TRADE','HTF_NEUTRAL','SWEPT','999','888','777','Entry:','Stop:','Target:'])
