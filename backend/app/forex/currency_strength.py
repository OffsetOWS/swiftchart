from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from time import monotonic
from typing import Mapping

import pandas as pd

from app.forex.config import ForexPairConfig
from app.forex.market_data import ForexMarketDataService
from app.forex.oanda import OandaForexProvider


CURRENCIES = ("USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD")
HORIZON_HOURS = {"1H": 1, "4H": 4, "24H": 24}
PAIR_CODES = (
    "EURUSD", "GBPUSD", "AUDUSD", "NZDUSD", "USDJPY", "USDCAD", "USDCHF",
    "EURGBP", "EURJPY", "EURCHF", "EURCAD", "EURAUD", "EURNZD",
    "GBPJPY", "GBPCHF", "GBPCAD", "GBPAUD", "GBPNZD",
    "AUDJPY", "AUDCHF", "AUDCAD", "AUDNZD",
    "NZDJPY", "NZDCHF", "NZDCAD", "CADJPY", "CADCHF", "CHFJPY",
)


def _pair_config(code: str) -> ForexPairConfig:
    quote = code[3:]
    return ForexPairConfig(
        pair=code,
        provider_symbol=f"{code[:3]}/{quote}",
        pip_size=0.01 if quote == "JPY" else 0.0001,
        relevant_sessions=(),
        max_spread_pips=0,
        min_atr_pips_1h=0,
        max_atr_pips_1h=10_000,
    )


CURRENCY_STRENGTH_PAIRS = {code: _pair_config(code) for code in PAIR_CODES}
_CACHE_TTL_SECONDS = 60
_cache: dict[str, tuple[float, dict]] = {}
_locks = {horizon: asyncio.Lock() for horizon in HORIZON_HOURS}


def normalize_strength_horizon(value: str) -> str:
    normalized = str(value or "24H").strip().upper()
    if normalized not in HORIZON_HOURS:
        raise ValueError("Currency strength horizon must be 1H, 4H, or 24H.")
    return normalized


def calculate_currency_strength(
    pair_changes: Mapping[str, float | None],
    *,
    horizon: str = "24H",
) -> dict:
    """Aggregate base/quote returns without feeding the signal engine."""

    selected_horizon = normalize_strength_horizon(horizon)
    contributions: dict[str, list[float]] = {currency: [] for currency in CURRENCIES}
    valid_changes: dict[str, float] = {}
    missing_pairs: list[str] = []

    for pair in PAIR_CODES:
        change = pair_changes.get(pair)
        if change is None or not pd.notna(change):
            missing_pairs.append(pair)
            continue
        value = float(change)
        valid_changes[pair] = value
        contributions[pair[:3]].append(value)
        contributions[pair[3:]].append(-value)

    raw_scores = {
        currency: (sum(values) / len(values) if values else None)
        for currency, values in contributions.items()
    }
    available_scores = [score for score in raw_scores.values() if score is not None]
    minimum = min(available_scores) if available_scores else None
    maximum = max(available_scores) if available_scores else None

    ranking: list[dict] = []
    for currency in CURRENCIES:
        raw_score = raw_scores[currency]
        if raw_score is None:
            normalized_score = None
        elif maximum == minimum:
            normalized_score = 50.0
        else:
            normalized_score = (raw_score - minimum) / (maximum - minimum) * 100
        ranking.append(
            {
                "currency": currency,
                "raw_score": round(raw_score, 5) if raw_score is not None else None,
                "normalized_score": round(normalized_score, 1) if normalized_score is not None else None,
                "available_pairs": len(contributions[currency]),
                "total_pairs": 7,
            }
        )

    ranking.sort(
        key=lambda row: (
            row["normalized_score"] is not None,
            row["normalized_score"] if row["normalized_score"] is not None else -1,
            row["currency"],
        ),
        reverse=True,
    )
    for index, row in enumerate(ranking, start=1):
        row["rank"] = index

    ranked_available = [row for row in ranking if row["normalized_score"] is not None]
    top = ranked_available[:3]
    bottom = list(reversed(ranked_available[-3:]))
    opportunities: list[dict] = []
    seen: set[str] = set()
    for strong in top:
        for weak in bottom:
            if strong["currency"] == weak["currency"]:
                continue
            pair = next(
                (
                    code for code in PAIR_CODES
                    if {code[:3], code[3:]} == {strong["currency"], weak["currency"]}
                ),
                None,
            )
            if pair is None or pair in seen or pair not in valid_changes:
                continue
            seen.add(pair)
            strong_is_base = pair[:3] == strong["currency"]
            opportunities.append(
                {
                    "pair": pair,
                    "direction": "BUY" if strong_is_base else "SELL",
                    "strong_currency": strong["currency"],
                    "weak_currency": weak["currency"],
                    "strength_gap": round(
                        float(strong["normalized_score"]) - float(weak["normalized_score"]), 1
                    ),
                    "pair_change_pct": round(valid_changes[pair], 4),
                }
            )
    opportunities.sort(key=lambda row: row["strength_gap"], reverse=True)

    return {
        "horizon": selected_horizon,
        "ranking": ranking,
        "opportunities": opportunities[:6],
        "available_pair_count": len(valid_changes),
        "total_pair_count": len(PAIR_CODES),
        "missing_pairs": missing_pairs,
    }


def _change_over_horizon(frame: pd.DataFrame, hours: int) -> float | None:
    if frame is None or frame.empty or len(frame) < 2:
        return None
    ordered = frame.sort_values("timestamp")
    timestamps = pd.DatetimeIndex(pd.to_datetime(ordered["timestamp"].tolist(), utc=True))
    target = timestamps[-1] - pd.Timedelta(hours=hours)
    earlier_positions = [index for index, timestamp in enumerate(timestamps) if timestamp <= target]
    if not earlier_positions:
        return None
    previous_close = float(ordered.iloc[earlier_positions[-1]]["close"])
    latest_close = float(ordered.iloc[-1]["close"])
    if previous_close <= 0:
        return None
    return (latest_close / previous_close - 1) * 100


async def _fetch_pair_change(
    service: ForexMarketDataService,
    pair: ForexPairConfig,
    *,
    hours: int,
    now: datetime,
    semaphore: asyncio.Semaphore,
) -> tuple[str, float | None]:
    async with semaphore:
        try:
            frame = await service.completed_candles(pair, "1H", limit=40, now=now)
            if not frame.empty:
                try:
                    completed_through = pd.to_datetime(frame["timestamp"], utc=True).max().to_pydatetime()
                    live = await service.live_candle(
                        pair,
                        "1H",
                        completed_through=completed_through,
                    )
                    if live:
                        frame = pd.concat([frame, pd.DataFrame([live])], ignore_index=True)
                except Exception:
                    # A forming candle improves freshness, but completed OANDA data
                    # remains a valid fallback during a transient quote failure.
                    pass
            return pair.pair, _change_over_horizon(frame, hours)
        except Exception:
            return pair.pair, None


async def get_currency_strength_snapshot(
    horizon: str = "24H",
    *,
    market_data: ForexMarketDataService | None = None,
    now: datetime | None = None,
) -> dict:
    selected_horizon = normalize_strength_horizon(horizon)
    current = (now or datetime.now(UTC)).astimezone(UTC)
    cached = _cache.get(selected_horizon)
    if market_data is None and cached and monotonic() - cached[0] < _CACHE_TTL_SECONDS:
        return cached[1]

    async with _locks[selected_horizon]:
        cached = _cache.get(selected_horizon)
        if market_data is None and cached and monotonic() - cached[0] < _CACHE_TTL_SECONDS:
            return cached[1]
        service = market_data or ForexMarketDataService(OandaForexProvider())
        semaphore = asyncio.Semaphore(5)
        moves = await asyncio.gather(*(
            _fetch_pair_change(
                service,
                pair,
                hours=HORIZON_HOURS[selected_horizon],
                now=current,
                semaphore=semaphore,
            )
            for pair in CURRENCY_STRENGTH_PAIRS.values()
        ))
        result = calculate_currency_strength(dict(moves), horizon=selected_horizon)
        result["updated_at"] = current.replace(microsecond=0).isoformat()
        result["provider"] = "oanda"
        result["informational_only"] = True
        result["includes_forming_candle"] = True
        result["refresh_seconds"] = _CACHE_TTL_SECONDS
        if market_data is None:
            _cache[selected_horizon] = (monotonic(), result)
        return result
