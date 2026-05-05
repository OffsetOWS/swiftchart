from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.routes.markets import router as markets_router
from app.routes.paper_trades import router as paper_trades_router
from app.routes.trade_history import router as trade_history_router
from app.services.scanner import start_background_scanner
from app.utils.database import init_db

settings = get_settings()

app = FastAPI(title=settings.app_name)
frontend_origins = [origin.strip() for origin in settings.frontend_origins.split(",") if origin.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=frontend_origins,
    allow_credentials="*" not in frontend_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup() -> None:
    init_db()
    start_background_scanner()


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
