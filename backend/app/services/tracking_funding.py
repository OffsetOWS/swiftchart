"""Historical hourly funding with an explicit entry-notional valuation estimate.

Hyperliquid settles at oracle prices. Its fundingHistory endpoint contains rates,
not historical oracle prices, so these modeled results are not wallet payments.
Exposure uses replay timestamps: entry included, an exit at the same time excluded.
"""
from datetime import UTC, datetime, timedelta
import math

from app.exchanges.hyperliquid import HyperliquidClient
from app.services.tracking_rules import timestamp, zone_bounds

HOUR = timedelta(hours=1)
BASIS = "historical-rates-entry-notional-estimate-v1"


def funding_r(row, outcome, rates):
    evidence = outcome.get("tracking_evidence") or {}
    entry_at = timestamp(evidence.get("intrabar", {}).get("entry_at") or evidence.get("validated_entry_at"))
    closed = timestamp(outcome.get("closed_at"))
    if not entry_at or not closed or evidence.get("category") != "resolved":
        return None
    fills = evidence.get("fills", [])
    if not fills or abs(sum(float(f['quantity']) for f in fills)-1) > 1e-12:
        return None
    low, high = zone_bounds(row["entry_zone_low"], row["entry_zone_high"])
    entry = (low+high)/2
    risk = abs(entry-float(row["stop_loss"]))
    if not math.isfinite(risk) or risk <= 0 or entry <= 0:
        return None
    start = entry_at.replace(minute=0,second=0,microsecond=0)
    if start < entry_at: start += HOUR
    expected = []
    while start < closed:
        expected.append(start)
        start += HOUR
    coin = row['symbol'][:-4] if row['symbol'].upper().endswith('USDT') else row['symbol']
    by_time = {}
    try:
        for rate in rates:
            at = datetime.fromtimestamp(int(rate['time'])/1000, UTC)
            if at not in expected: continue
            value = float(rate['fundingRate'])
            if rate['coin'] != coin or not math.isfinite(value): return None
            if at in by_time and by_time[at] != value: return None
            by_time[at] = value
    except (KeyError, ValueError, TypeError, OverflowError):
        return None
    if set(by_time) != set(expected):
        return None
    side = 1 if row['direction'] == 'LONG' else -1
    total = 0.0
    for at in expected:
        # Refined fills keep the fine candle confirmation times in evidence.
        quantity = 1-sum(float(f['quantity']) for f in fills if timestamp(f['candle_close_at']) <= at)
        total += side * entry * quantity * by_time[at] / risk
    return total


async def fetch_rates(row, outcome, cache):
    evidence = outcome.get('tracking_evidence') or {}
    entry = timestamp(evidence.get('intrabar', {}).get('entry_at') or evidence.get('validated_entry_at'))
    closed = timestamp(outcome.get('closed_at'))
    if row['exchange'] != 'hyperliquid' or not entry or not closed:
        return None
    if closed <= entry or closed-entry > timedelta(days=366):
        return None
    coin = row['symbol'][:-4] if row['symbol'].upper().endswith('USDT') else row['symbol']
    day = entry.replace(hour=0, minute=0, second=0, microsecond=0)
    client = HyperliquidClient()
    result = []
    while day < closed:
        key = (coin, day)
        if key not in cache:
            # Daily windows stay below the documented time-range response cap.
            cache[key] = await client._post_info(dict(type='fundingHistory', coin=coin,
                startTime=int(day.timestamp()*1000), endTime=int((day+timedelta(days=1)).timestamp()*1000)-1))
        rows = cache[key]
        if not isinstance(rows, list): return None
        result.extend(rows)
        day += timedelta(days=1)
    return result
