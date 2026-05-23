from __future__ import annotations

import logging
import re


SECRET_PATTERNS = [
    re.compile(r"(bot)[0-9]{6,}:[A-Za-z0-9_-]{20,}", re.IGNORECASE),
    re.compile(r"0x[a-fA-F0-9]{64}"),
    re.compile(r"(?i)(token|secret|private[_-]?key|api[_-]?key|password)(=)[\"']?([^,'\"\s}]+)[\"']?"),
    re.compile(r"(?i)(token|secret|private[_-]?key|api[_-]?key|password)(['\"\s:=]+)([^,'\"\s}]+)"),
    re.compile(r"(?i)(authorization|cookie|set-cookie)(['\"\s:=]+)([^,'\"\s}]+)"),
    re.compile(r"(?i)([?&](?:account_size|risk_per_trade_pct|min_rr|max_open_trades|accountSize|riskPerTradePct)=)([^&\s]+)"),
]


def redact_sensitive(value: object) -> str:
    text = str(value)
    for pattern in SECRET_PATTERNS:
        if pattern.groups >= 3:
            text = pattern.sub(r"\1\2[REDACTED]", text)
        elif pattern.groups == 2:
            text = pattern.sub(r"\1[REDACTED]", text)
        else:
            text = pattern.sub("[REDACTED]", text)
    return text


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact_sensitive(record.getMessage())
        record.args = ()
        return True


def install_secure_logging() -> None:
    root = logging.getLogger()
    if not any(isinstance(filter_, RedactingFilter) for filter_ in root.filters):
        root.addFilter(RedactingFilter())
    for handler in root.handlers:
        if not any(isinstance(filter_, RedactingFilter) for filter_ in handler.filters):
            handler.addFilter(RedactingFilter())
    for logger_name in ("uvicorn.access", "uvicorn.error", "httpx"):
        logger = logging.getLogger(logger_name)
        if not any(isinstance(filter_, RedactingFilter) for filter_ in logger.filters):
            logger.addFilter(RedactingFilter())
