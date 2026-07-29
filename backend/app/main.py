from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.middleware.rate_limit import InMemoryRateLimitMiddleware
from app.routes.genlayer import router as genlayer_router
from app.routes.market_intelligence import router as market_intelligence_router
from app.routes.markets import router as markets_router
from app.routes.paper_trades import router as paper_trades_router
from app.routes.trade_history import router as trade_history_router
from app.routes.forex import router as forex_router
from app.forex.scheduler import start_forex_worker
from app.forex.storage import ensure_forex_schema
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


@app.on_event("startup")
async def startup() -> None:
    install_secure_logging()
    init_db()
    ensure_forex_schema()
    start_background_scanner()
    start_forex_worker()


@app.get("/health")
def health():
    return {
        "status": "ok",
        "environment": settings.environment,
        "live_trading_enabled": settings.live_trading_enabled,
    }


app.include_router(markets_router, prefix="/api", tags=["markets"])
app.include_router(paper_trades_router, prefix="/api", tags=["paper-trades"])
app.include_router(trade_history_router, prefix="/api", tags=["trade-history"])
app.include_router(genlayer_router, prefix="/api", tags=["genlayer-ai"])
app.include_router(market_intelligence_router, prefix="/api", tags=["market-intelligence"])
app.include_router(forex_router, prefix="/api", tags=["forex"])
