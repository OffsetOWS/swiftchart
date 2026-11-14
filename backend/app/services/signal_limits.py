"""Admission limits preserve the caller's ranking; they do not rescore setups."""
from collections import Counter
from collections.abc import Iterable

from app.config import get_settings
from app.models.schemas import TradeIdea


def cap_signals_per_scan(ideas: Iterable[TradeIdea]) -> list[TradeIdea]:
    settings = get_settings()
    counts: Counter[str] = Counter()
    selected = []
    for idea in ideas:
        if len(selected) >= settings.crypto_max_signals_per_scan:
            break
        direction = str(idea.direction).upper()
        if counts[direction] >= settings.crypto_max_signals_per_direction:
            continue
        selected.append(idea)
        counts[direction] += 1
    return selected


def cap_production_cohorts(ideas: Iterable[TradeIdea]) -> list[TradeIdea]:
    """Bound ready signals without dropping research or pending-retest records."""
    rows = list(ideas)
    ready = [idea for idea in rows if idea.entry_status == "READY" and idea.strategy_decision in {None, "TRADE"}]
    admitted = {id(idea) for idea in cap_signals_per_scan(ready)}
    ready_ids = {id(idea) for idea in ready}
    return [idea for idea in rows if id(idea) not in ready_ids or id(idea) in admitted]
