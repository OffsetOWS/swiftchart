"""Configurable, tool-free xAI writer; Bedrock is an explicit opt-in fallback."""
import httpx
from time import perf_counter

from trading_agent.grok import _response_text
from trading_agent.usage import start_call, record_call


class GrokWriter:
    def __init__(self, settings, *, client=None):
        self.settings = settings
        self.client = client or httpx.AsyncClient(timeout=settings.grok_timeout_seconds, trust_env=False,
                                                 limits=httpx.Limits(max_connections=2, max_keepalive_connections=2))

    def register_tools(self):
        return ()  # Market writer cannot search or invoke any tool.

    async def respond(self, *, message, smart_context=None, detail=False, **ignored):
        if not self.settings.xai_api_key:
            raise RuntimeError('XAI_API_KEY is required for the Grok writer')
        model = self.settings.writer_detail_model if detail else self.settings.writer_default_model
        payload = {'model': model, 'store': False, 'tools': [],
                   'instructions': 'Write only from the authoritative API facts supplied. No searches, new facts, calculations, internal codes, or performance statistics. Follow the requested plain-English display shape exactly.',
                   'input': (smart_context or '') + '\n\n' + message,
                   'max_output_tokens': self.settings.detail_response_tokens if detail else self.settings.max_response_tokens}
        if detail and self.settings.writer_detail_reasoning_effort:
            payload['reasoning'] = {'effort': self.settings.writer_detail_reasoning_effort}
        trace = start_call(); started = perf_counter(); usage = None; status = 'failed'
        try:
            response = await self.client.post(self.settings.xai_api_base_url + '/responses',
                                              headers={'Authorization': 'Bearer ' + self.settings.xai_api_key}, json=payload)
            response.raise_for_status(); data = response.json(); usage = data.get('usage')
            text = _response_text(data)
            if data.get('status') == 'incomplete' or not text:
                raise RuntimeError('Incomplete Grok writer response')
            status = 'completed'
            return {'final_response': text}
        finally:
            record_call(trace, provider='xai', model=model, seconds=perf_counter()-started, usage=usage, status=status)


class WriterRouter:
    def __init__(self, settings, *, tools=None, primary=None, bedrock_factory=None):
        self.settings = settings
        self.tools = tools
        self.primary = primary or GrokWriter(settings)
        self.bedrock_factory = bedrock_factory
        self._bedrock = None

    def bedrock(self):
        if self._bedrock is None:
            if self.bedrock_factory:
                self._bedrock = self.bedrock_factory()
            else:
                from trading_agent.hermes import HermesOrchestrator
                self._bedrock = HermesOrchestrator(self.settings, tools=self.tools)
        return self._bedrock

    def register_tools(self):
        if self.settings.writer_provider == 'bedrock':
            return self.bedrock().register_tools()
        return ()

    async def _bedrock_response(self, kwargs):
        kwargs = {k: v for k, v in kwargs.items() if k != 'detail'}
        trace = start_call(); started = perf_counter(); status = 'failed'
        try:
            result = await self.bedrock().respond(**kwargs); status = 'completed'; return result
        finally:
            # Hermes currently exposes no billed-cost receipt. Unknown is not zero.
            record_call(trace, provider='bedrock', model=self.settings.bedrock_model_id,
                        seconds=perf_counter()-started, status=status)

    async def respond(self, **kwargs):
        if self.settings.writer_provider == 'bedrock':
            return await self._bedrock_response(kwargs)
        try:
            return await self.primary.respond(**kwargs)
        except Exception:
            if not self.settings.bedrock_fallback_enabled:
                raise
            return await self._bedrock_response(kwargs)
