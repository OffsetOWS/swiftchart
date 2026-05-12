from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass

from app.models.schemas import RiskSettings, TradeIdea
from app.services.market_data import get_candles_cached
from app.services.scanner import higher_timeframes_for
from app.strategy.trade_ideas import analyze_dataframe


DEFAULT_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT", "ADAUSDT", "AVAXUSDT"]
TIMEFRAME_MINUTES = {
    "30m": 30,
    "1h": 60,
    "2h": 120,
    "4h": 240,
    "6h": 360,
    "8h": 480,
    "12h": 720,
    "1d": 1440,
}


@dataclass
class ShortCheckResult:
    symbol: str
    regime: str
    bias: str
    before_shorts: list[TradeIdea]
    after_shorts: list[TradeIdea]
    rejected_short_reasons: list[str]


def candle_limit_for_two_days(timeframe: str) -> int:
    minutes = TIMEFRAME_MINUTES.get(timeframe.lower(), 240)
    two_day_candles = max(12, int((48 * 60) / minutes))
    return max(100, two_day_candles + 80)


async def analyze_symbol(exchange: str, symbol: str, timeframe: str, risk: RiskSettings) -> ShortCheckResult:
    limit = candle_limit_for_two_days(timeframe)
    df = await get_candles_cached(exchange, symbol, timeframe, limit)
    htf_dfs = []
    for htf in higher_timeframes_for(timeframe):
        try:
            htf_dfs.append(await get_candles_cached(exchange, symbol, htf, 220))
        except Exception:
            continue

    analysis = analyze_dataframe(symbol, timeframe, exchange, df, risk, htf_dfs)
    after_shorts = [idea for idea in analysis.trade_ideas if idea.direction == "Short"]
    before_shorts = [
        idea
        for idea in after_shorts
        if not (idea.regime_type == "TRANSITION_TO_BEARISH" and idea.is_regime_transition)
    ]
    rejected_short_reasons = [review.reason for review in analysis.rejected_signals if review.direction == "Short"]
    return ShortCheckResult(
        symbol=symbol,
        regime=analysis.market_regime_data.regime_type if analysis.market_regime_data else str(analysis.market_condition),
        bias=analysis.market_regime_data.bias if analysis.market_regime_data else "-",
        before_shorts=before_shorts,
        after_shorts=after_shorts,
        rejected_short_reasons=rejected_short_reasons,
    )


async def main() -> None:
    parser = argparse.ArgumentParser(description="Check SwiftChart short detection on the latest today/yesterday candles.")
    parser.add_argument("--exchange", default="hyperliquid")
    parser.add_argument("--timeframe", default="4h")
    parser.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS))
    parser.add_argument("--min-rr", type=float, default=2.0)
    args = parser.parse_args()

    symbols = [symbol.strip().upper() for symbol in args.symbols.split(",") if symbol.strip()]
    risk = RiskSettings(min_rr=args.min_rr)
    results = await asyncio.gather(
        *[analyze_symbol(args.exchange.lower(), symbol, args.timeframe.lower(), risk) for symbol in symbols],
        return_exceptions=True,
    )

    before_count = 0
    after_count = 0
    print(f"SwiftChart short-signal check exchange={args.exchange} timeframe={args.timeframe} window=latest today/yesterday candles")
    print("-" * 96)
    for symbol, result in zip(symbols, results, strict=False):
        if isinstance(result, Exception):
            print(f"{symbol}: error={result}")
            continue
        before_count += len(result.before_shorts)
        after_count += len(result.after_shorts)
        print(
            f"{result.symbol}: bias={result.bias} regime={result.regime} "
            f"shorts_before={len(result.before_shorts)} shorts_after={len(result.after_shorts)}"
        )
        for idea in result.after_shorts:
            print(
                f"  ACCEPTED short score={idea.setup_score} rr={idea.risk_reward_ratio} "
                f"entry={idea.entry_zone} stop={idea.stop_loss}"
            )
        for reason in result.rejected_short_reasons[:3]:
            print(f"  rejected_short={reason}")
    print("-" * 96)
    print(f"SUMMARY shorts_before={before_count} shorts_after={after_count} delta={after_count - before_count}")


if __name__ == "__main__":
    asyncio.run(main())
