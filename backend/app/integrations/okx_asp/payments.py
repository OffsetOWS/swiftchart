from __future__ import annotations

import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from decimal import Decimal, InvalidOperation

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from app.config import SUPPORTED_TIMEFRAMES, Settings
from app.integrations.okx_asp.auth import enforce_okx_public_rate_limit


logger = logging.getLogger("uvicorn.error")
PUBLIC_ANALYSIS_PATH = "/api/asp/okx/public/analyze-market"
SERVICE_DESCRIPTION = (
    "SwiftChart read-only multi-timeframe crypto market analysis. "
    "Provide symbol and timeframe as JSON."
)
_EVM_ADDRESS = re.compile(r"^0x[a-fA-F0-9]{40}$")


def _analysis_discovery_extensions() -> dict[str, object]:
    """Declare the POST JSON contract using the standard x402 Bazaar v2 shape."""
    input_schema = {
        "type": "object",
        "properties": {
            "symbol": {
                "type": "string",
                "description": "Supported crypto base asset or SwiftChart market symbol.",
                "minLength": 1,
                "maxLength": 30,
            },
            "timeframe": {
                "type": "string",
                "description": "Supported SwiftChart analysis timeframe.",
                "enum": SUPPORTED_TIMEFRAMES,
            },
        },
        "required": ["symbol", "timeframe"],
        "additionalProperties": False,
    }
    no_trade_example = {
        "status": "NO_TRADE",
        "symbol": "BTC",
        "timeframe": "4h",
        "direction": None,
        "score": None,
        "grade": "No Trade",
        "entry": None,
        "stopLoss": None,
        "takeProfit1": None,
        "takeProfit2": None,
        "riskReward": None,
        "marketBias": "NEUTRAL",
        "reasons": [],
    }
    return {
        "bazaar": {
            "info": {
                "input": {
                    "type": "http",
                    "method": "POST",
                    "bodyType": "json",
                    "body": {"symbol": "BTC", "timeframe": "4h"},
                },
                "output": {"type": "json", "example": no_trade_example},
            },
            "schema": {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "type": "object",
                "properties": {
                    "input": {
                        "type": "object",
                        "properties": {
                            "type": {"type": "string", "const": "http"},
                            "method": {"type": "string", "enum": ["POST"]},
                            "bodyType": {
                                "type": "string",
                                "enum": ["json", "form-data", "text"],
                            },
                            "body": input_schema,
                        },
                        "required": ["type", "method", "bodyType", "body"],
                        "additionalProperties": False,
                    },
                    "output": {
                        "type": "object",
                        "properties": {
                            "type": {"type": "string"},
                            "example": {"type": "object"},
                        },
                        "required": ["type"],
                    },
                },
                "required": ["input"],
            },
        }
    }


def _required_x402_settings(settings: Settings) -> None:
    missing = [
        name
        for name, value in (
            ("OKX_X402_API_KEY", settings.okx_x402_api_key),
            ("OKX_X402_SECRET_KEY", settings.okx_x402_secret_key),
            ("OKX_X402_PASSPHRASE", settings.okx_x402_passphrase),
            ("OKX_X402_PAY_TO_ADDRESS", settings.okx_x402_pay_to_address),
        )
        if not value.strip()
    ]
    if missing:
        raise RuntimeError(f"Missing required OKX x402 configuration: {', '.join(missing)}")
    if not _EVM_ADDRESS.fullmatch(settings.okx_x402_pay_to_address.strip()):
        raise RuntimeError("OKX_X402_PAY_TO_ADDRESS must be a valid EVM address.")
    if settings.okx_x402_network != "eip155:196":
        raise RuntimeError("SwiftChart OKX x402 payments must use X Layer mainnet (eip155:196).")
    if not settings.okx_x402_resource_url.startswith("https://"):
        raise RuntimeError("OKX_X402_RESOURCE_URL must use HTTPS.")
    try:
        price = Decimal(settings.okx_x402_price_usd)
    except InvalidOperation as exc:
        raise RuntimeError("OKX_X402_PRICE_USD must be a decimal amount.") from exc
    if price <= 0:
        raise RuntimeError("OKX_X402_PRICE_USD must be greater than zero.")


def install_okx_x402(app: FastAPI, settings: Settings, *, facilitator=None) -> bool:
    """Install the official OKX x402 middleware for the single public ASP route."""
    if not settings.okx_x402_enabled:
        return False

    _required_x402_settings(settings)

    from x402.http import (
        OKXAuthConfig,
        OKXFacilitatorClient,
        OKXFacilitatorConfig,
        PaymentOption,
        RouteConfig,
    )
    from x402.http.middleware.fastapi import payment_middleware
    from x402.mechanisms.evm.exact.server import ExactEvmScheme
    from x402.server import x402ResourceServer

    if facilitator is None:
        facilitator = OKXFacilitatorClient(
            OKXFacilitatorConfig(
                auth=OKXAuthConfig(
                    api_key=settings.okx_x402_api_key,
                    secret_key=settings.okx_x402_secret_key,
                    passphrase=settings.okx_x402_passphrase,
                ),
                base_url=settings.okx_x402_facilitator_base_url,
                sync_settle=True,
                timeout=settings.okx_x402_facilitator_timeout_seconds,
            )
        )

    server = x402ResourceServer(facilitator)
    server.register(settings.okx_x402_network, ExactEvmScheme())
    route_config = RouteConfig(
            accepts=[
                PaymentOption(
                    scheme="exact",
                    price=f"${settings.okx_x402_price_usd}",
                    network=settings.okx_x402_network,
                    pay_to=settings.okx_x402_pay_to_address,
                    max_timeout_seconds=300,
                )
            ],
            resource=settings.okx_x402_resource_url,
            description=SERVICE_DESCRIPTION,
            mime_type="application/json",
            extensions=_analysis_discovery_extensions(),
        )
    routes = {
        # OKX's official x402-check performs an initial no-body GET when it
        # discovers a submitted endpoint. Protect that discovery method with
        # the same SDK-generated challenge while preserving POST as the
        # parameterized service contract.
        f"GET {PUBLIC_ANALYSIS_PATH}": route_config,
        f"POST {PUBLIC_ANALYSIS_PATH}": route_config,
    }
    middleware = payment_middleware(
        routes,
        server,
        sync_facilitator_on_start=True,
    )
    app.middleware("http")(middleware)
    return True


def install_okx_asp_access_logging(app: FastAPI) -> None:
    """Log status and latency for the ASP route without logging bodies or secrets."""

    @app.middleware("http")
    async def okx_asp_access_log(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if request.url.path != PUBLIC_ANALYSIS_PATH:
            return await call_next(request)

        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        started = time.monotonic()
        try:
            enforce_okx_public_rate_limit(request)
        except HTTPException as exc:
            response = JSONResponse(
                status_code=exc.status_code,
                content={"detail": exc.detail},
                headers=exc.headers,
            )
        else:
            try:
                response = await call_next(request)
            except Exception:
                logger.exception(
                    "OKX ASP request failed request_id=%s method=%s",
                    request_id,
                    request.method,
                )
                raise

        elapsed_ms = round((time.monotonic() - started) * 1000)
        response.headers["X-Request-ID"] = request_id
        logger.info(
            "OKX ASP request request_id=%s method=%s status=%s duration_ms=%s",
            request_id,
            request.method,
            response.status_code,
            elapsed_ms,
        )
        return response
