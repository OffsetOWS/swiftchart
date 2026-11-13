from __future__ import annotations

from app.integrations.okx_asp.models import OKXAnalyzeMarketResponse, OKXEntryRange
from app.models.schemas import AnalysisResponse
from app.services.market_analysis import base_asset_symbol


BULLISH_REGIMES = {"TRENDING_UP", "BREAKOUT", "TRANSITION_TO_BULLISH"}
BEARISH_REGIMES = {"TRENDING_DOWN", "BREAKDOWN", "TRANSITION_TO_BEARISH"}


def _market_bias(analysis: AnalysisResponse) -> str:
    regime_type = analysis.market_regime_data.regime_type if analysis.market_regime_data else analysis.market_condition
    if regime_type in BULLISH_REGIMES:
        return "BULLISH"
    if regime_type in BEARISH_REGIMES:
        return "BEARISH"
    return "NEUTRAL"


def _unique_reasons(*groups: list[str | None]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for value in group:
            reason = str(value or "").strip()
            if reason and reason not in seen:
                seen.add(reason)
                output.append(reason)
    return output


def map_analysis_response(analysis: AnalysisResponse) -> OKXAnalyzeMarketResponse:
    symbol = base_asset_symbol(analysis.symbol)
    market_bias = _market_bias(analysis)
    idea = analysis.trade_ideas[0] if analysis.trade_ideas else None

    if idea is None:
        regime = analysis.market_regime_data
        reasons = _unique_reasons(
            [analysis.no_trade_reason, analysis.warning],
            [regime.bias_reason, regime.explanation] if regime else [],
        )
        return OKXAnalyzeMarketResponse(
            symbol=symbol,
            timeframe=analysis.timeframe,
            status="NO_TRADE",
            direction=None,
            score=None,
            grade="No Trade",
            entry=None,
            stop_loss=None,
            take_profit_1=None,
            take_profit_2=None,
            risk_reward=None,
            market_bias=market_bias,
            reasons=reasons,
        )

    reasons = _unique_reasons(
        [idea.reason],
        idea.reversal_confirmations,
        idea.downgraded_reasons,
    )
    return OKXAnalyzeMarketResponse(
        symbol=symbol,
        timeframe=idea.timeframe,
        status="TRADE",
        direction="LONG" if idea.direction == "Long" else "SHORT",
        score=idea.setup_score if idea.setup_score is not None else idea.confidence_score,
        grade=idea.setup_grade or "Valid Setup",
        entry=OKXEntryRange(low=idea.entry_zone[0], high=idea.entry_zone[1]),
        stop_loss=idea.stop_loss,
        take_profit_1=idea.take_profit_1,
        take_profit_2=idea.take_profit_2,
        risk_reward=idea.risk_reward_ratio,
        market_bias=market_bias,
        reasons=reasons,
    )
