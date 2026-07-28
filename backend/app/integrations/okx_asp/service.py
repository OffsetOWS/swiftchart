from __future__ import annotations

from app.config import get_settings
from app.integrations.okx_asp.mapper import map_analysis_response
from app.integrations.okx_asp.models import OKXAnalyzeMarketRequest, OKXAnalyzeMarketResponse
from app.models.schemas import RiskSettings
from app.services.market_analysis import analyze_market_read_only


async def analyze_market_for_okx(request: OKXAnalyzeMarketRequest) -> OKXAnalyzeMarketResponse:
    settings = get_settings()
    risk = RiskSettings(
        account_size=settings.default_account_size,
        risk_per_trade_pct=settings.default_risk_per_trade,
        min_rr=settings.default_min_rr,
        max_open_trades=settings.default_max_open_trades,
        preferred_timeframe=request.timeframe,
    )
    analysis = await analyze_market_read_only(
        exchange=settings.default_exchange,
        symbol=request.symbol,
        timeframe=request.timeframe,
        risk=risk,
        supplemental_timeout_seconds=settings.okx_asp_supplemental_data_timeout_seconds,
    )
    return map_analysis_response(analysis)
