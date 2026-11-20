from __future__ import annotations

import asyncio
from collections import defaultdict
from time import monotonic
import logging
import json
import re

from trading_agent.api import APIUnavailable
from trading_agent.config import TradingAgentSettings
from trading_agent.grok import GrokContextClient, needs_current_context
from trading_agent.hermes import HermesOrchestrator
from trading_agent.response_context import checked_reply, performance_request, render_signal, render_snapshot, safe_narrative
from trading_agent.session_store import ConversationStore
from trading_agent.tools import SwiftChartReadOnlyTools, UnsupportedMarket

logger = logging.getLogger('swiftchart.trading_agent.service')


class TradingAgentService:
    def __init__(self, settings: TradingAgentSettings, *, orchestrator=None, grok=None, sessions=None, tools=None):
        self.settings = settings
        self.tools = tools or SwiftChartReadOnlyTools(settings=settings)
        self.orchestrator = orchestrator or HermesOrchestrator(settings, tools=self.tools)
        self.grok = grok or GrokContextClient(settings)
        self.sessions = sessions or ConversationStore(settings.state_path, max_messages=settings.max_history_messages)
        self._locks = defaultdict(asyncio.Lock)
        self._turns = asyncio.Semaphore(8)
        self._models = asyncio.Semaphore(1)
        self._written = {}
        self._writing = {}

    async def _write_snapshot(self, data):
        """Model composes five lines; API-only template covers any failure.

        Coalesce the same API fact block across users without sharing history.
        A busy model cannot create an unbounded provider queue.
        """
        if not data.get('reply'):
            return render_snapshot(data)
        key = json.dumps(data['reply'], sort_keys=True)
        hit = self._written.get(key)
        if hit and monotonic() - hit[0] < 20:
            return hit[1]
        task = self._writing.get(key)
        if task is None:
            if self._models.locked():
                return render_snapshot(data)
            await self._models.acquire()
            async def write():
                try:
                    result = await self.orchestrator.respond(
                        message='Output only the following five-line market reply, keeping this order and every factual detail. You may replace a line with its alternative wording in the API facts. No introduction, markdown, extra sentences, calculations or tool calls.\n\n' + render_snapshot(data),
                        history=[], session_key='api-fact-writer', current_context=None,
                        smart_context='AUTHORITATIVE API DISPLAY FACTS (copy every number exactly; never expose internal codes): ' + key,
                    )
                    answer, accepted, reason = checked_reply(result.get('final_response'), data)
                    if not accepted:
                        logger.info('reply_template_fallback reason=%s', reason)
                    return answer
                except Exception as exc:
                    logger.warning('model_reply_unavailable type=%s', type(exc).__name__)
                    return render_snapshot(data)
                finally:
                    self._models.release()
            task = asyncio.create_task(write())
            self._writing[key] = task
            def complete(done):
                self._writing.pop(key, None)
                if not done.cancelled() and done.exception() is None:
                    if len(self._written) >= 128:
                        self._written.pop(next(iter(self._written)))
                    self._written[key] = (monotonic(), done.result())
            task.add_done_callback(complete)
        try:
            return await asyncio.wait_for(asyncio.shield(task), self.settings.hermes_timeout_seconds)
        except TimeoutError:
            return render_snapshot(data)

    @staticmethod
    def session_key(*, chat_id, user_id):
        return f'chat:{chat_id}:user:{user_id}'

    def reset(self, *, chat_id, user_id):
        self.sessions.reset(self.session_key(chat_id=chat_id, user_id=user_id))

    @staticmethod
    def _detect(text, markets):
        # Full pairs and $tickers take priority. Ambiguous short words must be
        # uppercase tickers, so "help me" does not become the ME market.
        for kind in ('forex', 'crypto'):
            for row in markets.get(kind, []):
                symbol, base = row['symbol'], row.get('base_asset', '')
                if re.search(r'(?<!\w)' + re.escape(symbol) + r'(?!\w)', text, re.I):
                    return kind, symbol
                if kind == 'forex' and '/' in text and symbol in re.sub(r'[^A-Z0-9]', '', text.upper()):
                    return kind, symbol
                if base and (re.search(r'\$' + re.escape(base) + r'\b', text, re.I) or re.search(r'\b' + re.escape(base) + r'\b', text, 0 if base in {'ME', 'ONE', 'ALL', 'NOT', 'FOR', 'THE', 'ON', 'YES', 'NO', 'NEW', 'TIME', 'NEWS', 'PRICE'} else re.I)):
                    return kind, symbol
        aliases = {'bitcoin': 'BTC', 'ethereum': 'ETH', 'gold': 'XAUUSD'}
        for word, target in aliases.items():
            if re.search(r'\b' + word + r'\b', text, re.I):
                for kind in ('forex', 'crypto'):
                    for row in markets.get(kind, []):
                        if row['symbol'] == target or row.get('base_asset') == target:
                            return kind, row['symbol']
        explicit = re.search(r'\$([A-Za-z0-9]+)\b', text)
        if explicit:
            return 'unknown', explicit[1]
        uppercase = re.findall(r'\b[A-Z]{2,12}\b', text)
        for word in uppercase:
            if word not in {'API', 'ETF', 'CPI', 'FOMC', 'NFP', 'SL', 'TP', 'USD', 'FOREX', 'CRYPTO'}:
                return 'unknown', word
        requested = re.search(r'\b(?:analy[sz]e|snapshot(?: for)?|levels? for|look at)\s+\$?([A-Za-z0-9/]+)', text, re.I)
        if requested and requested[1].lower() not in {'crypto', 'forex', 'markets', 'the', 'all'}:
            return 'unknown', requested[1]
        return None

    async def _explanation(self, text, history, key, *, news, facts=None):
        context = await self.grok.fetch(text) if news else None
        if news and context is None:
            return 'Live news is unavailable. I cannot verify current headlines.'
        try:
            await asyncio.wait_for(self._models.acquire(), timeout=self.settings.hermes_timeout_seconds)
        except TimeoutError:
            return ''
        task = asyncio.create_task(self.orchestrator.respond(
            message=text, history=history[-self.settings.relevant_history_messages:], session_key=key, current_context=context,
            smart_context='Explain qualitatively only. No numbers or trade decisions. API facts are rendered separately. AUTHORITATIVE API FACTS: ' + json.dumps(facts or {}, default=str),
        ))
        def finished(done):
            self._models.release()
            if not done.cancelled():
                done.exception()
        task.add_done_callback(finished)
        try:
            result = await asyncio.wait_for(asyncio.shield(task), self.settings.hermes_timeout_seconds)
            return safe_narrative(result.get('final_response'), news=news or facts is not None)
        except Exception as exc:
            logger.warning('model_reply_unavailable type=%s', type(exc).__name__)
            return ''

    async def _answer(self, text, history, key):
        if performance_request(text):
            return "I don't share performance stats. Ask me about a specific coin or signal."
        if re.search(r'\b(place|execute|cancel|submit|close)\s+(?:a |my |the )?(trade|order|position)\b', text, re.I):
            return 'I am read-only and cannot place, change, or cancel trades.'
        if re.fullmatch(r'(?:thanks?|thank you|ok(?:ay)?|got it|understood)[.! ]*', text, re.I):
            return 'Understood.'
        markets = await self.tools.get_markets()
        if re.search(r'\b(supported|coverage|which (?:coins|pairs|symbols))\b', text, re.I):
            return '\n'.join(kind.title() + ': ' + ', '.join(row['symbol'] for row in markets.get(kind, [])) for kind in ('crypto', 'forex'))
        if needs_current_context(text):
            note = await self._explanation(text, history, key, news=True)
            return 'News context (informational)\n' + (note or 'I cannot verify current headlines right now.') + '\nNot financial advice.'
        detected = self._detect(text, markets)
        if detected is None and re.search(r'\b(it|that|same|now|and|still|stop|invalidation|entry|targets?|price|levels?)\b', text, re.I):
            for message in reversed(history):
                if message.get('role') == 'user':
                    detected = self._detect(message.get('content', ''), markets)
                    if detected:
                        break
        if detected and detected[0] == 'unknown':
            await self.tools.resolve(detected[1])
        signal = re.search(r'\bsignal\s*(?:id\s*)?#?([A-Za-z0-9_-]*\d[A-Za-z0-9_-]*)\b', text, re.I)
        if signal:
            data = await self.tools.get_signal({'signal_id': signal[1], 'market_type': 'forex' if 'forex' in text.lower() or '-' in signal[1] else 'crypto'})
            return await self._write_snapshot(data) if data.get('available') is not False else 'That signal is not available in SwiftChart.'
        if 'strength' in text.lower():
            data = await self.tools.get_currency_strength({})
            return 'Currency strength\n' + '\n'.join(f'{row["currency"]}: {row.get("normalized_score", "unavailable")}' for row in data.get('ranking', []))
        if 'active' in text.lower() and re.search(r'\b(trades?|signals?)\b', text, re.I):
            kind = detected[0] if detected else ('forex' if re.search(r'\b(forex|fx)\b', text, re.I) else 'crypto' if 'crypto' in text.lower() else 'all')
            data = await self.tools.get_active_trades({'market_type': kind, 'symbol': detected[1] if detected else None})
            rows = [row for kind in ('forex', 'crypto') for row in data.get(kind, [])]
            return '\n\n'.join([await self._write_snapshot(row) for row in rows[:5]]) or 'No active signals were returned by SwiftChart.'
        if re.search(r'\bscans?\b', text, re.I):
            kind = detected[0] if detected else ('crypto' if 'crypto' in text.lower() else 'forex')
            method = self.tools.scan_crypto if kind == 'crypto' else self.tools.scan_forex
            args = {'symbol': detected[1]} if detected else {}
            timeframe = re.search(r'\b([0-9]+[mhdw])\b', text, re.I)
            if timeframe: args['timeframe'] = timeframe[1]
            data = await method(args)
            rows = data.get('signals', []) + data.get('pending_setups', [])
            return ('Latest saved SwiftChart scan\n' + '\n\n'.join([await self._write_snapshot(row) for row in rows[:5]])) if rows else 'No published setup is available in the latest SwiftChart scan.'
        news = needs_current_context(text)
        if detected:
            kind, symbol = detected
            timeframe = re.search(r'\b([0-9]+[mhdw])\b', text, re.I)
            args = {'symbol': symbol, 'market_type': kind}
            if timeframe:
                args['timeframe'] = timeframe[1]
            if 'volume' in text.lower() and 'profile' in text.lower():
                data = await self.tools.get_volume_profile(args)
                return ('Volume profile\n' + '\n'.join(f'{name.upper()}: {data[name]}' for name in ('poc', 'vah', 'val', 'delta_percent') if data.get(name) is not None)) if data.get('available') else 'SwiftChart volume profile is unavailable.'
            data = await self.tools.get_market_snapshot(args)
            return await self._write_snapshot(data)
        if news or re.search(r'\b(what is|explain|how does|how do)\b', text, re.I):
            note = await self._explanation(text, history, key, news=news)
            return note or 'I cannot validate that explanation right now. SwiftChart API facts remain authoritative.'
        return 'Which supported symbol or signal would you like me to check?'

    async def handle_message(self, *, chat_id, user_id, text):
        if not self.settings.is_authorized(chat_id=chat_id, user_id=user_id):
            return 'This trading-agent bot is private.'
        text = str(text).strip()[:4000]
        key = self.session_key(chat_id=chat_id, user_id=user_id)
        async with self._turns, self._locks[key]:
            history = await asyncio.to_thread(self.sessions.get_history, key)
            try:
                answer = await self._answer(text, history, key)
            except UnsupportedMarket as exc:
                answer = str(exc)
            except (APIUnavailable, ValueError):
                answer = 'SwiftChart data is temporarily unavailable. I cannot supply unverified prices or setups.'
            await asyncio.to_thread(self.sessions.replace_history, key, history + [{'role': 'user', 'content': text}, {'role': 'assistant', 'content': answer}])
            return answer
