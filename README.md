# SwiftChart

SwiftChart is a crypto market analysis dashboard that detects support/resistance, liquidity sweeps, range conditions, breakouts, and top trade ideas.

It is a full-stack, paper-first trading analysis app built with a FastAPI backend and a React/Vite frontend. SwiftChart does not place real trades by default.

## Features

- Binance OHLCV market data connector
- Hyperliquid OHLCV market data connector
- Modular exchange layer for future sources
- Swing high and swing low detection
- Horizontal support and resistance zones
- Liquidity sweep / stop-hunt detection
- Range, breakout, breakdown, trend, and no-trade-zone classification
- Top 5 trade idea scanner across liquid crypto pairs
- Risk settings for account size, risk per trade, max open trades, and minimum R:R
- Paper-trading ledger backed by SQLite
- Dark, responsive, Apple-inspired dashboard UI

## Project Structure

```text
backend/
  app/
    main.py
    config.py
    exchanges/
    strategy/
    models/
    routes/
    utils/
frontend/
  src/
    components/
    pages/
    styles/
    lib/
vercel.json
```

## Local Backend Setup

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload --port 8000
```

Backend URL:

```text
http://localhost:8000
```

API docs:

```text
http://localhost:8000/docs
```

## Local Frontend Setup

```bash
cd frontend
npm install
cp .env.example .env
npm run dev
```

Frontend URL:

```text
http://localhost:5173
```

## Environment Variables

Frontend:

```text
VITE_API_BASE=http://localhost:8000
```

Backend:

```text
APP_NAME=SwiftChart
ENVIRONMENT=development
DATABASE_URL=sqlite:///./swiftchart.db
BINANCE_BASE_URL=https://api.binance.com
HYPERLIQUID_BASE_URL=https://api.hyperliquid.xyz
FRONTEND_ORIGINS=http://localhost:5173,http://127.0.0.1:5173
LIVE_TRADING_ENABLED=false
DEFAULT_EXCHANGE=binance
DEFAULT_TIMEFRAME=4h
DEFAULT_ACCOUNT_SIZE=10000
DEFAULT_RISK_PER_TRADE=1
DEFAULT_MIN_RR=2
DEFAULT_MAX_OPEN_TRADES=3
```

## API Endpoints

```text
GET  /api/markets
GET  /api/candles?exchange=binance&symbol=SOLUSDT&timeframe=4h
GET  /api/analyze?symbol=SOLUSDT&timeframe=4h
GET  /api/top-ideas?timeframe=4h
POST /api/paper-trade
GET  /api/paper-trades
```

## Vercel Deployment

This repository is configured for Vercel to deploy the React/Vite frontend and expose the FastAPI backend through Vercel Python serverless functions.

Vercel settings:

```text
Install command: cd frontend && npm install
Build command: cd frontend && npm run build
Output directory: frontend/dist
```

The same settings are also defined in `vercel.json`.

Optional Vercel environment variable:

```text
VITE_API_BASE=
```

Leave `VITE_API_BASE` empty on Vercel to use same-origin API routes such as `/api/analyze`. For local development, `frontend/.env.example` points to `http://localhost:8000`.

## Backend Notes

The backend is available locally as a normal FastAPI app and in production through `api/index.py`, which imports `backend/app/main.py`.

The default production SQLite path is `/tmp/swiftchart.db` on Vercel serverless functions. That is fine for demo paper trades, but it is ephemeral. Use a hosted PostgreSQL or durable database for persistent production paper-trade history.

If you deploy the FastAPI backend separately, set:

```text
VITE_API_BASE=https://your-swiftchart-backend.example.com
FRONTEND_ORIGINS=https://your-swiftchart.vercel.app
```

## Production Build Check

```bash
cd frontend
npm run build
```

## Safety Note

SwiftChart produces potential trade setups only. These are not guaranteed profits. Crypto trading carries significant risk, and users are responsible for their own risk management.
