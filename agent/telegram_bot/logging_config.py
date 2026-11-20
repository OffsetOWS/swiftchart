from __future__ import annotations

from logging.handlers import RotatingFileHandler
import logging
from pathlib import Path

from trading_agent.config import TradingAgentSettings
from trading_agent.redaction import install_secure_logging, Redact


def configure_logging(settings: TradingAgentSettings) -> None:
    install_secure_logging()
    path = Path(settings.log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("swiftchart.trading_agent")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not any(
        isinstance(handler, RotatingFileHandler) and Path(handler.baseFilename) == path.resolve()
        for handler in logger.handlers
    ):
        handler = RotatingFileHandler(path, maxBytes=5_000_000, backupCount=5, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s | %(message)s"))
        handler.addFilter(Redact())
        logger.addHandler(handler)
    if not any(
        isinstance(handler, logging.StreamHandler)
        and not isinstance(handler, RotatingFileHandler)
        for handler in logger.handlers
    ):
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s | %(message)s")
        )
        stream_handler.addFilter(Redact())
        logger.addHandler(stream_handler)
    logging.getLogger("httpx").setLevel(logging.WARNING)
