"""User-requested price notifications only. All observed prices come from HTTP."""
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
import logging
import secrets
import asyncio

from trading_agent.private_state import PrivateState

logger = logging.getLogger('swiftchart.trading_agent.alerts')


def positive_price(value):
    try:
        price = Decimal(str(value).replace(',', ''))
    except InvalidOperation:
        raise ValueError('Enter a positive price') from None
    if not price.is_finite() or price <= 0 or len(str(price)) > 30:
        raise ValueError('Enter a positive finite price')
    return price


class AlertStore:
    def __init__(self, directory):
        self.state = PrivateState(directory)

    @staticmethod
    def _expire(data, now):
        for row in data.get('alerts', []):
            if row['state'] == 'active' and datetime.fromisoformat(row['expires_at']) <= now:
                row['state'] = 'expired'

    def create(self, user_id, chat_id, market, target, quote, *, now=None):
        now = now or datetime.now(UTC)
        target, current = positive_price(target), positive_price(quote['price'])
        try:
            observed = datetime.fromisoformat(quote['observed_at'].replace('Z', '+00:00'))
            fresh = 0 <= (now-observed).total_seconds() <= 60
        except (KeyError, ValueError, TypeError):
            fresh = False
        if not fresh:
            raise ValueError('A fresh SwiftChart price is unavailable; no alert was created.')
        with self.state.transaction(f'user:{user_id}') as data:
            self._expire(data, now)
            rows = data.setdefault('alerts', [])
            if sum(row['state'] == 'active' for row in rows) >= 10:
                raise ValueError('You already have ten active alerts. Cancel one with /alerts first.')
            row = {'id': secrets.token_urlsafe(6), 'user_id': user_id, 'chat_id': chat_id,
                   'symbol': market['symbol'], 'market_type': market['market_type'],
                   'timeframe': market['timeframe'], 'target': str(target),
                   'direction': 'above' if target >= current else 'below',
                   'created_at': now.isoformat(), 'expires_at': (now+timedelta(days=7)).isoformat(), 'state': 'active'}
            rows.append(row)
            data['alerts'] = rows[-200:]
        return dict(row)

    def list(self, user_id, *, now=None):
        with self.state.transaction(f'user:{user_id}') as data:
            self._expire(data, now or datetime.now(UTC))
            return [dict(row) for row in data.get('alerts', []) if row['state'] == 'active']

    def cancel(self, user_id, identity):
        with self.state.transaction(f'user:{user_id}') as data:
            for row in data.get('alerts', []):
                if row['id'] == identity and row['state'] == 'active':
                    row['state'] = 'cancelled'
                    return True
        return False

    def active(self, *, now=None):
        result = []
        for file in self.state.directory.glob('*.json'):
            # Use the same file-lock protocol without exposing identities in filenames.
            import json
            try:
                data = json.loads(file.read_text())
                rows = data.get('alerts', [])
                if rows:
                    result.extend(self.list(rows[0]['user_id'], now=now))
            except (ValueError, OSError, KeyError):
                logger.warning('alert_state_unavailable')
        return result

    def claim_hit(self, user_id, identity, quote, *, now=None):
        now = now or datetime.now(UTC)
        price = positive_price(quote['price'])
        try:
            observed = datetime.fromisoformat(quote['observed_at'].replace('Z', '+00:00'))
            if not 0 <= (now-observed).total_seconds() <= 60:
                return None
        except (KeyError, ValueError, TypeError):
            return None
        with self.state.transaction(f'user:{user_id}') as data:
            self._expire(data, now)
            for row in data.get('alerts', []):
                if row['id'] != identity or row['state'] != 'active':
                    continue
                hit = price >= positive_price(row['target']) if row['direction'] == 'above' else price <= positive_price(row['target'])
                if hit:
                    # Claim before the network call. Never resend after an ambiguous
                    # Telegram timeout/restart: Bot API has no idempotency key.
                    row.update(state='sending', fired_at=now.isoformat(), hit_price=str(price))
                    return dict(row)
        return None

    def delivered(self, user_id, identity, *, success):
        with self.state.transaction(f'user:{user_id}') as data:
            for row in data.get('alerts', []):
                if row['id'] == identity and row['state'] == 'sending':
                    row['state'] = 'delivered' if success else 'delivery_uncertain'


async def deliver_alerts(service, bot, *, now=None):
    rows = await asyncio.to_thread(service.alerts.active, now=now)
    if not rows:
        return 0
    delivered = 0
    # Group the quote request; do not analyse a whole chart for every alert.
    for kind in ('crypto', 'forex'):
        selected = [row for row in rows if row['market_type'] == kind and service.settings.is_authorized(user_id=row['user_id'], chat_id=row['chat_id'])]
        if not selected:
            continue
        try:
            symbols = list(dict.fromkeys(row['symbol'] for row in selected))
            data = {'quotes':{}}
            for offset in range(0,len(symbols),100):
                batch = await service.tools.get_prices({'market_type': kind, 'symbols': symbols[offset:offset+100]})
                data['quotes'].update(batch.get('quotes',{}))
        except Exception as exc:
            logger.warning('alert_quotes_unavailable type=%s', type(exc).__name__)
            continue
        for row in selected:
            quote = data.get('quotes', {}).get(row['symbol'])
            if not quote:
                continue
            hit = await asyncio.to_thread(service.alerts.claim_hit, row['user_id'], row['id'], quote, now=now)
            if not hit:
                continue
            success = False
            try:
                await bot.send_message(chat_id=hit['chat_id'], text=f"Price alert: {hit['symbol']} reached your target {hit['target']}.\nSwiftChart price: {quote['display_price']}. Data: {quote['observed_at']}.\nNot financial advice.")
                success = True
                delivered += 1
            except Exception as exc:
                logger.warning('alert_delivery_uncertain type=%s', type(exc).__name__)
            finally:
                await asyncio.to_thread(service.alerts.delivered, row['user_id'], row['id'], success=success)
    return delivered
