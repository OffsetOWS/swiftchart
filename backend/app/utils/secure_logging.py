from __future__ import annotations

import logging
import re


SECRET_PATTERNS = [
    re.compile(r"(bot)[0-9]{6,}:[A-Za-z0-9_-]{20,}", re.IGNORECASE),
    re.compile(r"0x[a-fA-F0-9]{64}"),
    re.compile(r"(?i)(token|secret|private[_-]?key|api[_-]?key|password)(['\"\s:=]+)([^,'\"\s}]+)"),
]


def redact_sensitive(value: object) -> str:
    text = str(value)
    for pattern in SECRET_PATTERNS:
        if pattern.groups >= 3:
            text = pattern.sub(r"\1\2[REDACTED]", text)
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
