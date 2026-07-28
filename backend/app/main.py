from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.middleware.rate_limit import InMemoryRateLimitMiddleware
from app.integrations.okx_asp.payments import install_okx_asp_access_logging, install_okx_x402
from app.routes.ea import router as ea_router
from app.routes.genlayer import router as genlayer_router
from app.routes.forex import router as forex_router
from app.routes.market_intelligence import router as market_intelligence_router
from app.routes.markets import router as markets_router
from app.routes.mt5 import router as mt5_router
from app.routes.okx_asp import router as okx_asp_router
from app.routes.paper_trades import router as paper_trades_router
from app.routes.payments import router as payments_router
from app.routes.trade_history import router as trade_history_router
from app.services.scanner import start_background_scanner
from app.utils.database import init_db
from app.utils.secure_logging import install_secure_logging

settings = get_settings()

app = FastAPI(title=settings.app_name)
frontend_origins = [origin.strip() for origin in settings.frontend_origins.split(",") if origin.strip()]

app.add_middleware(InMemoryRateLimitMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=frontend_origins,
    allow_credentials="*" not in frontend_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)
install_okx_x402(app, settings)
install_okx_asp_access_logging(app)


@app.on_event("startup")
async def startup() -> None:
    install_secure_logging()
    init_db()
    if get_settings().crypto_background_scanner_enabled:
        start_background_scanner()


@app.get("/health")
def health():
    return {
        "status": "ok",
        "environment": settings.environment,
        "live_trading_enabled": settings.live_trading_enabled,
    }


app.include_router(markets_router, prefix="/api", tags=["markets"])
app.include_router(forex_router, prefix="/api", tags=["forex"])
app.include_router(ea_router, prefix="/api", tags=["mql5-ea"])
app.include_router(mt5_router, prefix="/api", tags=["mt5-execution"])
app.include_router(okx_asp_router, prefix="/api", tags=["okx-asp"])
app.include_router(paper_trades_router, prefix="/api", tags=["paper-trades"])
app.include_router(payments_router, prefix="/api", tags=["payments"])
app.include_router(trade_history_router, prefix="/api", tags=["trade-history"])
app.include_router(genlayer_router, prefix="/api", tags=["genlayer-ai"])
app.include_router(market_intelligence_router, prefix="/api", tags=["market-intelligence"])
