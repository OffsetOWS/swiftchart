"""Public Telegram deep links contain only canonical signal identifiers."""
import re


def signal_agent_url(kind, identity, username='swift_agent_bot'):
    if kind not in {'crypto', 'forex'} or not re.fullmatch(r'[A-Za-z0-9_]{5,32}', username):
        raise ValueError('Invalid agent link')
    identity = str(identity)
    payload = f's_{"c" if kind == "crypto" else "f"}_{identity}'
    if len(payload) > 64 or not re.fullmatch(r'[A-Za-z0-9_-]+', payload):
        raise ValueError('Invalid signal identity')
    return f'https://t.me/{username}?start={payload}'
