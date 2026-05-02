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
- Telegram bot for analysis, top trade ideas, and strategy education

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
bot/
  main.py
  handlers.py
  keyboards.py
  formatter.py
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
TELEGRAM_BOT_TOKEN=
BINANCE_API_KEY=
BINANCE_API_SECRET=
HYPERLIQUID_API_KEY=
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

## Telegram Bot

SwiftChart Bot lets users request the same analysis engine from Telegram. It is analysis-only and paper-trading only; it never places real trades.

Supported commands:

```text
/start
/analyze SOLUSDT 4h
/top
/strategy
/help
```

Supported timeframes:

```text
30m, 1h, 2h, 4h, 6h, 8h, 12h, 1D
```

### Create the Telegram Bot

1. Open Telegram and message `@BotFather`.
2. Run `/newbot`.
3. Set the display name to `SwiftChart Bot`.
4. Choose a unique username ending in `bot`.
5. Copy the bot token from BotFather.

### Run the Bot Locally

```bash
cd bot
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Add your token:

```text
TELEGRAM_BOT_TOKEN=your_botfather_token
```

Run from the project root so the bot can import the existing backend strategy modules:

```bash
cd ..
bot/.venv/bin/python -m bot.main
```

### Bot Environment Variables

```text
TELEGRAM_BOT_TOKEN=
BINANCE_API_KEY=
BINANCE_API_SECRET=
HYPERLIQUID_API_KEY=
APP_NAME=SwiftChart
DEFAULT_EXCHANGE=hyperliquid
DEFAULT_TIMEFRAME=4h
DEFAULT_ACCOUNT_SIZE=10000
DEFAULT_RISK_PER_TRADE=1
DEFAULT_MIN_RR=2
DEFAULT_MAX_OPEN_TRADES=3
BINANCE_BASE_URL=https://api.binance.com
HYPERLIQUID_BASE_URL=https://api.hyperliquid.xyz
```

The current Binance and Hyperliquid candle connectors use public OHLCV endpoints. API key variables are included for future authenticated extensions, but live trading remains disabled.

### Deploy the Bot Online

Deploy the `bot/` worker separately from the Vercel website on a Python-capable host such as Render, Railway, Fly.io, or a VPS.

Recommended worker start command:

```bash
python -m bot.main
```

Recommended deployment notes:

- Set the working directory to the repository root.
- Install dependencies from `bot/requirements.txt`.
- Set `TELEGRAM_BOT_TOKEN` in the host environment.
- Keep `LIVE_TRADING_ENABLED=false`.
- Use `DEFAULT_EXCHANGE=hyperliquid` if Binance blocks your host region.
- The bot uses polling, so it should run as a background worker/service.

## Vercel Deployment

This repository is configured for Vercel to deploy the React/Vite frontend and expose the FastAPI backend through Vercel Python serverless functions.

The Telegram bot is not deployed on Vercel. Run it separately as a worker on Render, Railway, Fly.io, or a VPS.

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
