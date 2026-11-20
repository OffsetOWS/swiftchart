"""Agent logs metadata only; provider HTTP/debug output stays disabled."""
import logging
import re


class Redact(logging.Filter):
    def filter(self, record):
        text = record.getMessage()
        text = re.sub(r'\b[0-9]{5,20}:[A-Za-z0-9_-]{30,}', '[REDACTED]', text)
        text = re.sub(r'\bxai-[A-Za-z0-9_-]+', '[REDACTED]', text)
        text = re.sub(r'(?i)(bearer\s+|(?:token|secret|api_key)=)\S+', '[REDACTED]', text)
        record.msg, record.args, record.exc_info, record.exc_text = text, (), None, None
        return True


def install_secure_logging():
    for name in ('httpx', 'httpcore', 'telegram', 'botocore', 'boto3'):
        logging.getLogger(name).setLevel(logging.WARNING)
    logger = logging.getLogger('swiftchart.trading_agent')
    if not any(isinstance(f, Redact) for f in logger.filters):
        logger.addFilter(Redact())
