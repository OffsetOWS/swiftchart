# SwiftChart

SwiftChart is a crypto market analysis dashboard that detects support/resistance, liquidity sweeps, range conditions, breakouts, and top trade ideas.

It is a full-stack, paper-first trading analysis app built with a FastAPI backend and a React/Vite frontend. SwiftChart does not place real trades by default.

## Features

- Binance OHLCV market data connector
- Hyperliquid OHLCV market data connector with HIP-3 perp DEX symbol support
- Modular exchange layer for future sources
- Swing high and swing low detection
- Scored support and resistance zones with touches, reaction strength, volume response, and recency
- Confirmed liquidity sweep / stop-hunt detection
- RANGE_BOUND, TRENDING_UP, TRENDING_DOWN, BREAKOUT, BREAKDOWN, CHOP, and NO_TRADE regime classification
- Multi-timeframe bias filter
- Setup scoring and grading; only 65/100+ trade ideas are shown
- Top 5 trade idea scanner across liquid crypto pairs
- Exchange filter for Binance, Hyperliquid, or all supported exchanges
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
HYPERLIQUID_HIP3_DEXES=
HYPERLIQUID_SCAN_LIMIT=40
FRONTEND_ORIGINS=http://localhost:5173,http://127.0.0.1:5173
LIVE_TRADING_ENABLED=false
DEFAULT_EXCHANGE=binance
DEFAULT_TIMEFRAME=4h
DEFAULT_ACCOUNT_SIZE=10000
DEFAULT_RISK_PER_TRADE=1
DEFAULT_MIN_RR=2
DEFAULT_MAX_OPEN_TRADES=3
TRADE_HISTORY_EXPIRY_BARS=12
TELEGRAM_BOT_TOKEN=
TELEGRAM_WEBHOOK_URL=
TELEGRAM_WEBHOOK_SECRET=
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
GET  /api/trade-history
GET  /api/trade-history/{id}
POST /api/trade-history/check
GET  /api/trade-stats
```

## Trade History and Outcome Tracking

SwiftChart saves every generated trade idea as an immutable historical analysis record. Saved records keep the original entry zone, stop, targets, score, reason, and invalidation even if the strategy changes later.

Trade history is paginated and sorted newest-first by default:

```text
GET /api/trade-history?page=1&limit=20&sort=desc
```

Optional filters include `symbol`, `exchange`, `timeframe`, `direction`, `status`, `result`, `date_from`, and `date_to`. Empty filters do not hide old, pending, open, expired, or no-entry records.

Outcome checking fetches later candles and updates:

```text
PENDING
ENTRY_TRIGGERED
TP1_HIT
TP2_HIT
SL_HIT
EXPIRED
INVALIDATED
AMBIGUOUS
```

Results are:

```text
WIN
PARTIAL_WIN
LOSS
NO_ENTRY
AMBIGUOUS
OPEN
```

If TP and SL occur inside the same candle, SwiftChart marks the result `AMBIGUOUS` unless lower-timeframe data is available to resolve order. Ambiguous outcomes are not counted as wins or losses.

Manual outcome check:

```bash
curl -X POST http://localhost:8000/api/trade-history/check
```

## Telegram Bot

SwiftChart Bot lets users request the same analysis engine from Telegram. It is analysis-only and paper-trading only; it never places real trades.

Supported commands:

```text
/start
/analyze SOLUSDT 4h
/top
/subscribe
/unsubscribe
/alerts
/history
/stats
/checktrades
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
TELEGRAM_WEBHOOK_URL=
TELEGRAM_WEBHOOK_SECRET=
TELEGRAM_ALERT_CHAT_IDS=
ALERTS_ENABLED=true
ALERT_EXCHANGE=hyperliquid
ALERT_TIMEFRAME=4h
ALERT_SCAN_INTERVAL_SECONDS=1800
ALERTS_RUN_SECRET=
BOT_STATE_PATH=.swiftchart_bot_state.json
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

### Hyperliquid HIP-3 Markets

Hyperliquid HIP-3 markets use the normal `/info` candle snapshot endpoint, but candles require the DEX-prefixed coin name such as `xyz:XYZ100`. SwiftChart supports this format internally and exposes HIP-3 markets through the Hyperliquid exchange connector.

If the public `perpDexs` metadata endpoint is unavailable from your host, set known HIP-3 DEX names manually:

```text
HYPERLIQUID_HIP3_DEXES=xyz,flx
HYPERLIQUID_SCAN_LIMIT=40
```

You can analyze a HIP-3-only market by selecting Hyperliquid or All Exchanges and using either the displayed symbol from `/api/markets?exchange=hyperliquid` or a DEX-prefixed symbol such as `xyz:XYZ100USDT`.

### Telegram Trade Alerts

Users can subscribe from Telegram:

```text
/subscribe
```

SwiftChart scans for valid setups and sends alerts only for new ideas that pass the strategy score threshold. It deduplicates already-sent alerts with `BOT_STATE_PATH`.

Useful alert settings:

```text
ALERTS_ENABLED=true
ALERT_EXCHANGE=hyperliquid
ALERT_TIMEFRAME=4h
ALERT_SCAN_INTERVAL_SECONDS=1800
ALERTS_RUN_SECRET=choose_a_long_random_string
```

You can also pin alert recipients with `TELEGRAM_ALERT_CHAT_IDS`, a comma-separated list of Telegram chat IDs. This is useful on free hosts where local state can reset.

The webhook app exposes a manual scan endpoint:

```text
https://your-render-service.onrender.com/alerts/run?secret=your_alert_secret
```

Use this with an external cron/wake service if the Render free service sleeps. Render Cron Jobs are available but have a minimum monthly charge, and Render background workers are not free.

### Deploy the Bot on Render

Render's free tier supports free web services. Background workers are not free, so SwiftChart Bot includes a webhook web service for Render and keeps polling available for local testing.

The repo includes `render.yaml` with:

```text
Build command: pip install -r bot/requirements.txt
Start command: uvicorn bot.webhook:app --host 0.0.0.0 --port $PORT
Service type: Web Service
Plan: Free
```

Recommended Render steps:

1. Rotate your BotFather token before deploying if it was ever exposed in local logs.
2. Go to Render, click **New**, then **Blueprint**.
3. Connect the GitHub repo `OffsetOWS/swiftchart`.
4. Select the `main` branch and let Render read `render.yaml`.
5. Add environment variables:

```text
TELEGRAM_BOT_TOKEN=your_botfather_token
TELEGRAM_WEBHOOK_SECRET=choose_a_long_random_string
DEFAULT_EXCHANGE=hyperliquid
DEFAULT_TIMEFRAME=4h
LIVE_TRADING_ENABLED=false
ALERTS_ENABLED=true
ALERT_EXCHANGE=hyperliquid
ALERT_TIMEFRAME=4h
ALERT_SCAN_INTERVAL_SECONDS=1800
ALERTS_RUN_SECRET=choose_a_long_random_string
```

`TELEGRAM_WEBHOOK_URL` is optional on Render because the bot uses Render's `RENDER_EXTERNAL_URL` automatically. If you set it manually, use:

```text
https://your-render-service.onrender.com/telegram/webhook
```

After the first successful deploy, open:

```text
https://your-render-service.onrender.com/health
```

It should return `{"status":"ok"}`. Then message your bot on Telegram with `/start`.

Free Render web services can spin down after idle time and wake back up on the next incoming request. For truly always-on instant replies and scheduled scans, use an external cron to hit `/alerts/run`, upgrade the service, or use a paid worker/VPS.

If Render logs show `can't use getUpdates method while webhook is active`, the service is running the local polling entrypoint by mistake. Update the Render service start command to:

```text
uvicorn bot.webhook:app --host 0.0.0.0 --port $PORT
```

Do not use `python -m bot.main` for the hosted Render web service. That command is only for local polling tests.

## Vercel Deployment

This repository is configured for Vercel to deploy the React/Vite frontend and expose the FastAPI backend through Vercel Python serverless functions.

The Telegram bot is not deployed on Vercel. Run it separately as a Render web service, Railway service, Fly.io app, or VPS process.

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
