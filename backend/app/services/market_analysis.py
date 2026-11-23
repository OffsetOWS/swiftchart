from __future__ import annotations

import asyncio
from dataclasses import dataclass

from app.config import get_settings
from app.services.crypto_work import run_crypto_work
from app.models.schemas import AnalysisResponse, RiskSettings
from app.services.liquidity_filter import skip_low_volume_market
from app.services.market_data import get_candles_cached, get_markets_cached, read_cached_candles
from app.services.scanner import selected_exchanges as scan_selected_exchanges
from app.strategy.market_regime import regime_score_from_dataframe
from app.strategy.trade_ideas import analyze_dataframe


QUOTE_SUFFIXES = ("USDT", "USDC", "SUSD", "USD", "PERP")


@dataclass(slots=True)
class MarketAnalysisUnavailable(Exception):
    found_market: bool
    last_error: object | None = None


def selected_exchange(exchange: str | None) -> str:
    return (exchange or get_settings().default_exchange).lower()


def base_asset_symbol(symbol: str) -> str:
    normalized = "".join(character for character in str(symbol or "").upper() if character.isalnum())
    for suffix in QUOTE_SUFFIXES:
        if normalized.endswith(suffix) and len(normalized) > len(suffix):
            return normalized[: -len(suffix)]
    return normalized


def symbol_for_exchange(exchange: str, symbol: str) -> str:
    base_asset = base_asset_symbol(symbol)
    if exchange.lower() == "hyperliquid":
        return f"{base_asset}USDT"
    return str(symbol or "").strip().upper()


async def market_for_symbol(exchange: str, symbol: str) -> dict | None:
    normalized_symbol = symbol_for_exchange(exchange, symbol)
    for market in await get_markets_cached(exchange):
        if str(market.get("symbol", "")).upper() == normalized_symbol:
            return market
    return None


def higher_timeframes_for(timeframe: str) -> list[str]:
    normalized = timeframe.lower()
    if normalized in {"30m", "1h"}:
        return ["4h", "1d"]
    if normalized in {"2h", "4h", "6h", "8h", "12h"}:
        return ["1d"]
    return []


async def global_regime_score(exchange: str, timeframe: str) -> float | None:
    from app.services.score_archive import EXTERNAL_CONTEXT,candle_snapshot
    EXTERNAL_CONTEXT.set([])
    scores = []
    frames=[]
    for symbol in ("BTCUSDT", "ETHUSDT"):
        try:
            df = await get_candles_cached(exchange, symbol, timeframe, 260)
            if len(df) >= 80:
                scores.append(await run_crypto_work(regime_score_from_dataframe, df))
                frames.append(await run_crypto_work(candle_snapshot, df, exchange=exchange, symbol=symbol, timeframe=timeframe))
        except Exception:
            continue
    EXTERNAL_CONTEXT.set(frames)
    if not scores:
        return None
    return round(sum(scores) / len(scores), 1)


async def analyze_market_read_only(
    *,
    exchange: str,
    symbol: str,
    timeframe: str,
    risk: RiskSettings,
    supplemental_timeout_seconds: float | None = None,
    cached_context_only: bool = False,
) -> AnalysisResponse:
    """Run SwiftChart's existing single-market analysis without persistence or delivery side effects."""
    from app.services.score_archive import EXTERNAL_CONTEXT
    EXTERNAL_CONTEXT.set(None)
    exchanges = scan_selected_exchanges(selected_exchange(exchange))
    last_error: object | None = None
    found_market = False

    for current_exchange in exchanges:
        selected_symbol = symbol_for_exchange(current_exchange, symbol)
        try:
            market = await market_for_symbol(current_exchange, selected_symbol)
            if market is None:
                last_error = f"{selected_symbol} is not listed on {current_exchange}."
                continue
            found_market = True
            if skip_low_volume_market(market):
                last_error = "Perp 24h volume is below the scanner liquidity minimum."
                continue
            df = await get_candles_cached(current_exchange, selected_symbol, timeframe, 320)
            if len(df) < 80:
                last_error = "Not enough candle history for analysis."
                continue
            async def supplemental_data():
                htf_results = await asyncio.gather(
                    *[
                        get_candles_cached(current_exchange, selected_symbol, htf, 240)
                        for htf in higher_timeframes_for(timeframe)
                    ],
                    return_exceptions=True,
                )
                htf_dfs = [result for result in htf_results if not isinstance(result, Exception)]
                regime_score = await global_regime_score(current_exchange, timeframe)
                return htf_dfs, regime_score

            try:
                if cached_context_only:
                    htf_dfs = [frame for tf in higher_timeframes_for(timeframe)
                               if (frame := read_cached_candles(current_exchange, selected_symbol, tf, 240)) is not None]
                    scores = []
                    for asset in ('BTCUSDT', 'ETHUSDT'):
                        context = read_cached_candles(current_exchange, asset, timeframe, 260)
                        if context is not None:
                            scores.append(await run_crypto_work(regime_score_from_dataframe, context))
                    regime_score = round(sum(scores)/len(scores), 1) if scores else None
                elif supplemental_timeout_seconds is None:
                    htf_dfs, regime_score = await supplemental_data()
                else:
                    async with asyncio.timeout(supplemental_timeout_seconds):
                        htf_dfs, regime_score = await supplemental_data()
            except TimeoutError:
                htf_dfs, regime_score = [], None
            df.attrs["market_metadata"] = market
            analysis = await run_crypto_work(
                analyze_dataframe, selected_symbol,
                timeframe,
                current_exchange,
                df,
                risk,
                htf_dfs,
                global_regime_score=regime_score,
            )
            from app.services.liquidity_filter import perp_volume_24h
            for idea in analysis.trade_ideas: idea.market_volume_24h = perp_volume_24h(market)
            return analysis
        except Exception as exc:
            last_error = exc
            continue

    raise MarketAnalysisUnavailable(found_market=found_market, last_error=last_error)
