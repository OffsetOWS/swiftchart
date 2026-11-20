from __future__ import annotations

import asyncio
from collections import defaultdict
from time import monotonic
import logging
import json
import re
from dataclasses import dataclass
from time import perf_counter
from contextvars import ContextVar

from trading_agent.api import APIUnavailable
from trading_agent.config import TradingAgentSettings
from trading_agent.grok import GrokContextClient, needs_current_context
from trading_agent.hermes import HermesOrchestrator
from trading_agent.response_context import checked_reply, performance_request, render_signal, render_snapshot, reply_options, safe_narrative
from trading_agent.private_state import MarketContexts
from trading_agent.alerts import AlertStore, positive_price
from trading_agent.session_store import ConversationStore
from trading_agent.tools import SwiftChartReadOnlyTools, UnsupportedMarket

logger = logging.getLogger('swiftchart.trading_agent.service')
_model_result = ContextVar('model_written_reply', default=False)


@dataclass
class AgentReply:
    text: str
    context: dict | None = None
    data: dict | None = None
    model_written: bool = False
    seconds: float = 0


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
        self._model_written = {}
        self.contexts = MarketContexts(settings.state_path.parent / 'market-contexts')
        self.alerts = AlertStore(settings.state_path.parent / 'price-alerts')

    async def _write_snapshot(self, data, *, detail=False, timeout=None):
        """Model composes the chosen view; API-only template covers any failure.

        Coalesce the same API fact block across users without sharing history.
        A busy model cannot create an unbounded provider queue.
        """
        _model_result.set(False)
        if not data.get('reply'):
            return render_snapshot(data, detail=detail)
        facts = {**data['reply'], 'line_options': reply_options(data, detail=detail)}
        key = json.dumps(facts, sort_keys=True)
        hit = self._written.get(key)
        if hit and monotonic() - hit[0] < 20:
            _model_result.set(self._model_written.get(key, False))
            return hit[1]
        task = self._writing.get(key)
        if task is None:
            if self._models.locked():
                return render_snapshot(data, detail=detail)
            await self._models.acquire()
            async def write():
                try:
                    result = await self.orchestrator.respond(
                        message=f'Write this market reply in {len(facts["line_options"])} lines, keeping the supplied factual detail and order. You may use alternative allowed wording. Plain English, no internal codes, introduction, markdown, calculations or tool calls.\n\n' + render_snapshot(data, detail=detail),
                        history=[], session_key='api-fact-writer', current_context=None,
                        smart_context='AUTHORITATIVE API DISPLAY FACTS (copy every number exactly; never expose internal codes): ' + key,
                    )
                    answer, accepted, reason = checked_reply(result.get('final_response'), data, detail=detail)
                    self._model_written[key] = accepted
                    if not accepted:
                        logger.info('reply_template_fallback reason=%s', reason)
                    return answer
                except Exception as exc:
                    logger.warning('model_reply_unavailable type=%s', type(exc).__name__)
                    self._model_written[key] = False
                    return render_snapshot(data, detail=detail)
                finally:
                    self._models.release()
            task = asyncio.create_task(write())
            self._writing[key] = task
            def complete(done):
                self._writing.pop(key, None)
                if not done.cancelled() and done.exception() is None:
                    if len(self._written) >= 128:
                        self._written.pop(next(iter(self._written)))
                    if len(self._model_written) >= 128:
                        self._model_written.pop(next(iter(self._model_written)))
                    self._written[key] = (monotonic(), done.result())
            task.add_done_callback(complete)
        try:
            text = await asyncio.wait_for(asyncio.shield(task), timeout or self.settings.hermes_timeout_seconds)
            _model_result.set(self._model_written.get(key, False))
            return text
        except TimeoutError:
            return render_snapshot(data, detail=detail)

    async def warm_up(self):
        """Warm provider credentials and a factual reply before accepting users."""
        try:
            markets = await self.tools.get_markets()
            first = next((row for row in markets.get('crypto', []) if row.get('base_asset') == 'BTC'), None)
            if first:
                data = await self.tools.get_market_snapshot({'symbol': first['symbol'], 'market_type': 'crypto', 'timeframe': '4h'})
                await self._write_snapshot(data)
        except Exception as exc:
            logger.warning('agent_warmup_unavailable type=%s', type(exc).__name__)

    async def _market_reply(self, chat_id, user_id, market, *, token=None, detail=False, budget=None, data=None):
        started = perf_counter()
        args = {k: market[k] for k in ('symbol', 'market_type', 'timeframe')}
        args['detail'] = detail
        if data is None:
            fetch = self.tools.get_market_snapshot(args)
            data = await asyncio.wait_for(fetch, max(0.1, budget-1.0)) if budget else await fetch
        remaining = max(.1, budget-(perf_counter()-started)) if budget else None
        text = await self._write_snapshot(data, detail=detail, timeout=remaining)
        if token is None:
            token = await asyncio.to_thread(self.contexts.create, chat_id, user_id, market)
        context = await asyncio.to_thread(self.contexts.update, chat_id, user_id, token, detail=detail)
        model = _model_result.get()
        return AgentReply(text, context=context, data=data, model_written=model, seconds=perf_counter()-started)

    @staticmethod
    def _timeframe(text):
        match = re.search(r'\b(1|4)\s*(?:h|hour(?:ly|s)?)\b|\b(1)\s*d\b', text, re.I)
        if match:
            return (match[1]+'h') if match[1] else '1d'
        if re.search(r'\bdaily\b', text, re.I): return '1d'
        if re.search(r'\bhourly\b', text, re.I): return '1h'
        return None

    async def _interactive_answer(self, chat_id, user_id, text, history, key):
        if performance_request(text):
            return "I don't share performance stats. Ask me about a specific coin or signal."
        if re.search(r'\b(place|execute|cancel|submit|close)\s+(?:a |my |the )?(trade|order|position)\b', text, re.I):
            return 'I am read-only and cannot place, change, or cancel trades.'
        previous = await asyncio.to_thread(self.contexts.get, chat_id, user_id)
        target = re.search(r'\balert\b.*?\b(?:reaches?|hits?|at|above|below)\s*\$?([0-9][0-9,.]*)', text, re.I)
        if previous and previous.get('awaiting_alert') and re.fullmatch(r'\s*\$?[0-9][0-9,.]*\s*', text):
            return await self.create_alert(chat_id, user_id, previous, text.strip().lstrip('$'))
        if needs_current_context(text):
            query = text + (f' about {previous["symbol"]}' if previous and previous['symbol'] not in text else '')
            note = await self._explanation(query, history, key, news=True)
            return AgentReply('News context (informational)\n'+(note or 'I cannot verify current headlines right now.')+'\nNot financial advice.', previous)
        markets = await self.tools.get_markets()
        followup = bool(re.fullmatch(r'\s*(?:why\??|more detail[.!?]?|and (?:the )?daily\??|and (?:the )?hourly\??)\s*', text, re.I))
        detected = None if followup else self._detect(text, markets)
        timeframe = self._timeframe(text)
        if detected and detected[0] == 'unknown':
            if previous and timeframe and detected[1] in {'DAILY','HOURLY'}:
                detected = None
            elif not re.search(r'\bsignal\b', text, re.I):
                await self.tools.resolve(detected[1])
        signal = re.search(r'\bsignal\s*(?:id\s*)?#?([A-Za-z0-9_-]*\d[A-Za-z0-9_-]*)\b', text, re.I)
        if signal:
            return await self.open_signal(chat_id, user_id, 'forex' if '-' in signal[1] or 'forex' in text.lower() else 'crypto', signal[1])
        if detected and detected[0] in {'crypto','forex'}:
            market = {'market_type':detected[0], 'symbol':detected[1], 'timeframe':timeframe or (previous or {}).get('timeframe') or ('1h' if detected[0]=='forex' else '4h')}
        elif previous and (followup or timeframe or target or re.search(r'\b(it|that|same|now|and|levels?|price|invalidation)\b', text, re.I)):
            market = {k:previous[k] for k in ('symbol','market_type','timeframe')}
            if timeframe: market['timeframe'] = timeframe
        else:
            return await self._answer(text, history, key)
        if target:
            return await self.create_alert(chat_id, user_id, market, target[1])
        detail = bool(re.search(r'\b(more detail|detailed|explain|why|full)\b', text, re.I))
        return await self._market_reply(chat_id, user_id, market, detail=detail, budget=None if detail else 4.0)

    async def interact(self, *, chat_id, user_id, token, action):
        async with self._turns, self._locks[self.session_key(chat_id=chat_id,user_id=user_id)]:
            return await self._interact(chat_id=chat_id,user_id=user_id,token=token,action=action)

    async def _interact(self, *, chat_id, user_id, token, action):
        if not self.settings.is_authorized(chat_id=chat_id, user_id=user_id):
            raise ValueError('This trading-agent bot is private.')
        context = await asyncio.to_thread(self.contexts.get, chat_id, user_id, token)
        if context is None:
            raise ValueError('That market reply has expired. Ask for a fresh snapshot.')
        context = await asyncio.to_thread(self.contexts.update, chat_id, user_id, token)
        if action == 'alert':
            context = await asyncio.to_thread(self.contexts.update, chat_id, user_id, token, awaiting_alert=True)
            return AgentReply(f'At what price should I alert you for {context["symbol"]}? Send a price, or “Alert me if {context["symbol"]} reaches …”.', context)
        if action == 'chart':
            data = await self.tools.get_chart(context)
            return AgentReply('Chart from SwiftChart API data.', context, data=data)
        if action == 'news':
            note = await self._explanation(f'News about {context["symbol"]}. Give a short qualitative summary without figures or trading instructions.', [], self.session_key(chat_id=chat_id,user_id=user_id), news=True)
            return AgentReply(f'News context for {context["symbol"]} (informational)\n'+(note or 'I cannot verify current headlines right now.')+'\nNot financial advice.', context)
        if action.startswith('tf'):
            timeframe = action[2:]
            if timeframe not in {'1h','4h','1d'}:raise ValueError('Unsupported timeframe')
            context = await asyncio.to_thread(self.contexts.update, chat_id, user_id, token, timeframe=timeframe)
        elif action == 'detail':
            context = await asyncio.to_thread(self.contexts.update, chat_id, user_id, token, detail=not context.get('detail',False))
        else:
            raise ValueError('Unsupported market action')
        return await self._market_reply(chat_id, user_id, context, token=token, detail=context.get('detail',False))

    async def open_signal(self, chat_id, user_id, kind, identity):
        if not self.settings.is_authorized(chat_id=chat_id,user_id=user_id):
            raise ValueError('This trading-agent bot is private.')
        data = await self.tools.get_signal_context({'market_type':kind,'signal_id':identity})
        if data.get('available') is False:
            return AgentReply('That signal is not available in SwiftChart.')
        market = {'symbol':data['symbol'],'timeframe':data['timeframe'],'market_type':kind,'signal_id':identity}
        return await self._market_reply(chat_id,user_id,market,data=data,detail=True)

    async def create_alert(self, chat_id, user_id, market, target):
        if not self.settings.is_authorized(chat_id=chat_id,user_id=user_id):
            raise ValueError('This trading-agent bot is private.')
        target = positive_price(target)
        prices = await self.tools.get_prices({'market_type':market['market_type'],'symbols':[market['symbol']]})
        quote = prices.get('quotes',{}).get(market['symbol'])
        if not quote:raise ValueError('A fresh SwiftChart price is unavailable; no alert was created.')
        row = await asyncio.to_thread(self.alerts.create,user_id,chat_id,market,target,quote)
        context = market if market.get('token') else None
        if not context:
            token = await asyncio.to_thread(self.contexts.create,chat_id,user_id,market)
            context = await asyncio.to_thread(self.contexts.get,chat_id,user_id,token)
        if context:
            context = await asyncio.to_thread(self.contexts.update,chat_id,user_id,context['token'],awaiting_alert=False)
        return AgentReply(f'Alert set: {row["symbol"]} at {row["target"]} (your target).\nExpires {row["expires_at"][:10]}. One notification when reached. Manage it with /alerts.',context)

    @staticmethod
    def session_key(*, chat_id, user_id):
        return f'chat:{chat_id}:user:{user_id}'

    def reset(self, *, chat_id, user_id):
        self.sessions.reset(self.session_key(chat_id=chat_id, user_id=user_id))
        self.contexts.reset(chat_id,user_id)

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
        requested = re.search(r'\b(?:analy[sz]e|snapshot(?: for)?|levels? for|look at|what about|how about)\s+\$?([A-Za-z0-9/]+)', text, re.I)
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
            smart_context='Use two short qualitative sentences. No numerical claims, amounts, percentages, dates, prices, counts, trading instructions or URLs. API market facts are rendered separately. AUTHORITATIVE API FACTS: ' + json.dumps(facts or {}, default=str),
        ))
        def finished(done):
            self._models.release()
            if not done.cancelled():
                done.exception()
        task.add_done_callback(finished)
        try:
            result = await asyncio.wait_for(asyncio.shield(task), self.settings.hermes_timeout_seconds)
            note = safe_narrative(result.get('final_response'), news=news or facts is not None)
            if note and news and context and context.sources:
                note += '\nSources: ' + ' '.join(context.sources[:2])
            return note
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

    async def handle_turn(self, *, chat_id, user_id, text):
        if not self.settings.is_authorized(chat_id=chat_id, user_id=user_id):
            return AgentReply('This trading-agent bot is private.')
        text = str(text).strip()[:4000]
        key = self.session_key(chat_id=chat_id, user_id=user_id)
        async with self._turns, self._locks[key]:
            history = await asyncio.to_thread(self.sessions.get_history, key)
            try:
                turn = self._interactive_answer(chat_id,user_id,text,history,key)
                slow_action = needs_current_context(text) or bool(re.search(r'\b(alert|more detail|detailed|explain|why|full|signal)\b',text,re.I))
                answer = await turn if slow_action else await asyncio.wait_for(turn,4.2)
            except UnsupportedMarket as exc:
                answer = str(exc)
            except ValueError as exc:
                answer = str(exc)
            except (APIUnavailable, TimeoutError):
                answer = 'SwiftChart data is temporarily unavailable. I cannot supply unverified prices or setups.'
            result = answer if isinstance(answer,AgentReply) else AgentReply(answer)
            await asyncio.to_thread(self.sessions.replace_history, key, history + [{'role': 'user', 'content': text}, {'role': 'assistant', 'content': result.text}])
            return result

    async def handle_message(self, **kwargs):
        return (await self.handle_turn(**kwargs)).text
