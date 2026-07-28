from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import ValidationError

from app.config import get_settings
from app.exchanges.base import MarketDataUnavailable
from app.integrations.okx_asp.auth import enforce_okx_asp_rate_limit, enforce_okx_public_rate_limit
from app.integrations.okx_asp.models import OKXAnalyzeMarketRequest, OKXAnalyzeMarketResponse
from app.integrations.okx_asp.service import analyze_market_for_okx
from app.services.market_analysis import MarketAnalysisUnavailable, base_asset_symbol


router = APIRouter()
logger = logging.getLogger(__name__)
MAX_PUBLIC_REQUEST_BYTES = 1024


@router.post("/asp/okx/analyze-market", response_model=OKXAnalyzeMarketResponse)
async def okx_analyze_market(
    request: OKXAnalyzeMarketRequest,
    _: str = Depends(enforce_okx_asp_rate_limit),
):
    try:
        return await analyze_market_for_okx(request)
    except MarketAnalysisUnavailable as unavailable:
        if isinstance(unavailable.last_error, MarketDataUnavailable):
            raise HTTPException(status_code=503, detail=str(unavailable.last_error)) from unavailable
        symbol = base_asset_symbol(request.symbol)
        raise HTTPException(
            status_code=422,
            detail=f"{symbol or 'This asset'} is not currently available for analysis.",
        ) from unavailable
    except MarketDataUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("OKX ASP analysis failed symbol=%s timeframe=%s", request.symbol, request.timeframe)
        raise HTTPException(status_code=502, detail="Could not analyze market. Please try again shortly.") from exc


async def _validated_public_request(request: Request) -> OKXAnalyzeMarketRequest:
    raw_content_type = request.headers.get("content-type", "")
    content_type = raw_content_type.split(";", 1)[0].strip().lower()
    # Some standards-compliant x402 buyers replay the original JSON body with a
    # PAYMENT-SIGNATURE header but omit Content-Type. Keep the endpoint JSON-only
    # by validating the body below, while still rejecting any explicitly wrong
    # media type.
    if raw_content_type and content_type != "application/json":
        raise HTTPException(status_code=415, detail="Content-Type must be application/json.")
    declared_size = request.headers.get("content-length")
    if declared_size:
        try:
            if int(declared_size) > MAX_PUBLIC_REQUEST_BYTES:
                raise HTTPException(status_code=413, detail="Request body is too large.")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid Content-Length header.") from exc

    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_PUBLIC_REQUEST_BYTES:
            raise HTTPException(status_code=413, detail="Request body is too large.")
    try:
        payload = json.loads(body)
        return OKXAnalyzeMarketRequest.model_validate(payload)
    except (json.JSONDecodeError, UnicodeDecodeError, ValidationError, TypeError) as exc:
        raise HTTPException(status_code=422, detail="Invalid analyze_market request.") from exc


@router.post(
    "/asp/okx/public/analyze-market",
    response_model=OKXAnalyzeMarketResponse,
    dependencies=[Depends(enforce_okx_public_rate_limit)],
)
async def okx_public_analyze_market(request: Request):
    settings = get_settings()
    if settings.environment == "production" and not settings.okx_x402_enabled:
        raise HTTPException(status_code=503, detail="OKX payment protection is not configured.")
    payload = await _validated_public_request(request)
    try:
        async with asyncio.timeout(settings.okx_asp_analysis_timeout_seconds):
            return await analyze_market_for_okx(payload)
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail="SwiftChart analysis timed out.") from exc
    except MarketAnalysisUnavailable as unavailable:
        if isinstance(unavailable.last_error, MarketDataUnavailable):
            raise HTTPException(status_code=503, detail=str(unavailable.last_error)) from unavailable
        symbol = base_asset_symbol(payload.symbol)
        raise HTTPException(
            status_code=422,
            detail=f"{symbol or 'This asset'} is not currently available for analysis.",
        ) from unavailable
    except MarketDataUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("OKX public ASP analysis failed symbol=%s timeframe=%s", payload.symbol, payload.timeframe)
        raise HTTPException(status_code=502, detail="Could not analyze market. Please try again shortly.") from exc


@router.get(
    "/asp/okx/public/analyze-market",
    response_model=OKXAnalyzeMarketResponse,
    dependencies=[Depends(enforce_okx_public_rate_limit)],
)
async def okx_public_analyze_market_discovery():
    """Read-only compatibility fallback for OKX's no-body x402 validator."""
    settings = get_settings()
    if settings.environment == "production" and not settings.okx_x402_enabled:
        raise HTTPException(status_code=503, detail="OKX payment protection is not configured.")
    payload = OKXAnalyzeMarketRequest(symbol="BTC", timeframe="4h")
    try:
        async with asyncio.timeout(settings.okx_asp_analysis_timeout_seconds):
            return await analyze_market_for_okx(payload)
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail="SwiftChart analysis timed out.") from exc
    except (MarketAnalysisUnavailable, MarketDataUnavailable) as exc:
        raise HTTPException(status_code=503, detail="SwiftChart market data is temporarily unavailable.") from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("OKX public ASP discovery analysis failed")
        raise HTTPException(status_code=502, detail="Could not analyze market. Please try again shortly.") from exc
