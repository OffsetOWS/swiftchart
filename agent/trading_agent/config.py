from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


def _integer_set(name: str) -> frozenset[int]:
    values: set[int] = set()
    for raw_value in os.getenv(name, "").split(","):
        value = raw_value.strip()
        if not value:
            continue
        try:
            values.add(int(value))
        except ValueError as exc:
            raise RuntimeError(f"{name} must contain comma-separated Telegram numeric IDs.") from exc
    return frozenset(values)


def _positive_float(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a number.") from exc
    if value <= 0:
        raise RuntimeError(f"{name} must be greater than zero.")
    return value


def _positive_int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer.") from exc
    if value <= 0:
        raise RuntimeError(f"{name} must be greater than zero.")
    return value


@dataclass(frozen=True)
class TradingAgentSettings:
    telegram_bot_token: str
    allowed_user_ids: frozenset[int]
    allowed_chat_ids: frozenset[int]
    state_path: Path
    log_path: Path
    aws_region: str
    bedrock_model_id: str
    bedrock_endpoint_url: str
    hermes_agent_path: Path | None
    xai_api_key: str
    xai_api_base_url: str
    grok_model: str
    grok_timeout_seconds: float
    max_history_messages: int
    max_tool_iterations: int
    max_response_tokens: int = 512
    tool_timeout_seconds: float = 12.0
    hermes_timeout_seconds: float = 45.0
    cache_ttl_seconds: float = 20.0
    relevant_history_messages: int = 8
    zone_near_percent: float = 0.003
    hermes_home: Path = Path(".swiftchart_trading_agent_hermes")

    api_base_url: str = "http://127.0.0.1:8000"
    api_token: str = ""
    writer_provider: str = 'xai'
    writer_default_model: str = 'grok-4.20-0309-non-reasoning'
    writer_detail_model: str = 'grok-4.3'
    writer_detail_reasoning_effort: str = 'none'
    detail_response_tokens: int = 1024
    bedrock_fallback_enabled: bool = False
    daily_question_limit: int = 30
    daily_limit_timezone: str = 'Africa/Lagos'
    news_cache_seconds: float = 900.0
    news_reasoning_effort: str = 'low'
    x_search_post_limit: int = 8

    @classmethod
    def from_env(cls) -> "TradingAgentSettings":
        aws_region = (
            os.getenv("TRADING_AGENT_AWS_REGION")
            or os.getenv("AWS_REGION")
            or os.getenv("AWS_DEFAULT_REGION")
            or ""
        ).strip()
        endpoint = os.getenv("TRADING_AGENT_BEDROCK_ENDPOINT_URL", "").strip()
        if not endpoint:
            endpoint = f"https://bedrock-runtime.{aws_region}.amazonaws.com"
        hermes_path = os.getenv("HERMES_AGENT_PATH", "").strip()
        return cls(
            writer_provider=os.getenv('TRADING_AGENT_WRITER_PROVIDER', 'xai').strip().lower(),
            writer_default_model=os.getenv('TRADING_AGENT_WRITER_DEFAULT_MODEL', 'grok-4.20-0309-non-reasoning').strip(),
            writer_detail_model=os.getenv('TRADING_AGENT_WRITER_DETAIL_MODEL', 'grok-4.3').strip(),
            writer_detail_reasoning_effort=os.getenv('TRADING_AGENT_WRITER_DETAIL_REASONING_EFFORT', 'none').strip(),
            detail_response_tokens=_positive_int('TRADING_AGENT_DETAIL_RESPONSE_TOKENS', 1024),
            bedrock_fallback_enabled=os.getenv('TRADING_AGENT_BEDROCK_FALLBACK_ENABLED', 'false').lower() == 'true',
            daily_question_limit=_positive_int('TRADING_AGENT_DAILY_QUESTION_LIMIT', 30),
            daily_limit_timezone=os.getenv('TRADING_AGENT_DAILY_LIMIT_TIMEZONE', 'Africa/Lagos'),
            news_cache_seconds=_positive_float('TRADING_AGENT_NEWS_CACHE_SECONDS', 900.0),
            news_reasoning_effort=os.getenv('TRADING_AGENT_NEWS_REASONING_EFFORT', 'low').strip(),
            x_search_post_limit=min(8, _positive_int('TRADING_AGENT_X_SEARCH_POST_LIMIT', 8)),
            api_base_url=os.getenv("SWIFTCHART_API_BASE_URL", "http://127.0.0.1:8000"),
            api_token=os.getenv("AGENT_INTERNAL_API_TOKEN", ""),
            telegram_bot_token=os.getenv("TRADING_AGENT_TELEGRAM_BOT_TOKEN", "").strip(),
            allowed_user_ids=_integer_set("TRADING_AGENT_ALLOWED_USER_IDS"),
            allowed_chat_ids=_integer_set("TRADING_AGENT_ALLOWED_CHAT_IDS"),
            state_path=Path(os.getenv("TRADING_AGENT_STATE_PATH", "/var/lib/swiftchart-agent/sessions")),
            log_path=Path(os.getenv("TRADING_AGENT_LOG_PATH", "/var/log/swiftchart-agent/agent.log")),
            aws_region=aws_region,
            bedrock_model_id=os.getenv(
                "TRADING_AGENT_BEDROCK_MODEL_ID",
                "",
            ).strip(),
            bedrock_endpoint_url=endpoint,
            hermes_agent_path=Path(hermes_path).expanduser() if hermes_path else None,
            xai_api_key=os.getenv("XAI_API_KEY", "").strip(),
            xai_api_base_url=os.getenv("XAI_API_BASE_URL", "https://api.x.ai/v1").rstrip("/"),
            grok_model=os.getenv("TRADING_AGENT_GROK_MODEL", "grok-4.6").strip(),
            grok_timeout_seconds=_positive_float("TRADING_AGENT_GROK_TIMEOUT_SECONDS", 50.0),
            max_history_messages=_positive_int("TRADING_AGENT_MAX_HISTORY_MESSAGES", 40),
            max_tool_iterations=_positive_int("TRADING_AGENT_MAX_TOOL_ITERATIONS", 4),
            max_response_tokens=_positive_int("TRADING_AGENT_MAX_RESPONSE_TOKENS", 512),
            tool_timeout_seconds=_positive_float("TRADING_AGENT_TOOL_TIMEOUT_SECONDS", 12.0),
            hermes_timeout_seconds=_positive_float("TRADING_AGENT_HERMES_TIMEOUT_SECONDS", 45.0),
            cache_ttl_seconds=_positive_float("TRADING_AGENT_CACHE_TTL_SECONDS", 20.0),
            relevant_history_messages=_positive_int("TRADING_AGENT_RELEVANT_HISTORY_MESSAGES", 8),
            zone_near_percent=_positive_float("TRADING_AGENT_ZONE_NEAR_PERCENT", 0.003),
            hermes_home=Path(
                os.getenv("TRADING_AGENT_HERMES_HOME", ".swiftchart_trading_agent_hermes")
            ),
        )

    def validate_for_bot(self) -> None:
        if not self.api_token:
            raise RuntimeError("AGENT_INTERNAL_API_TOKEN is required.")
        if self.state_path.suffix.lower() in {".db", ".sqlite", ".sqlite3"}:
            raise RuntimeError("Conversation state must be a JSON directory, not a database.")
        if not self.telegram_bot_token:
            raise RuntimeError("TRADING_AGENT_TELEGRAM_BOT_TOKEN is required.")
        if not self.allowed_user_ids and not self.allowed_chat_ids:
            raise RuntimeError(
                "Configure TRADING_AGENT_ALLOWED_USER_IDS or TRADING_AGENT_ALLOWED_CHAT_IDS; "
                "the trading-agent bot denies access by default."
            )
        if self.writer_provider not in {'xai', 'bedrock'}:
            raise RuntimeError('TRADING_AGENT_WRITER_PROVIDER must be xai or bedrock.')
        if self.writer_provider == 'xai' and (not self.xai_api_key or not self.writer_default_model or not self.writer_detail_model):
            raise RuntimeError('XAI_API_KEY and Grok writer models are required.')
        if (self.writer_provider == 'bedrock' or self.bedrock_fallback_enabled) and (not self.aws_region or not self.bedrock_model_id):
            raise RuntimeError('AWS region and TRADING_AGENT_BEDROCK_MODEL_ID are required for enabled Bedrock.')
        from zoneinfo import ZoneInfo
        ZoneInfo(self.daily_limit_timezone)

    def is_authorized(self, *, user_id: int | None, chat_id: int | None) -> bool:
        return bool(
            (user_id is not None and user_id in self.allowed_user_ids)
            or (chat_id is not None and chat_id in self.allowed_chat_ids)
        )
