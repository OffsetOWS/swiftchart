# SwiftChart Execution Bot

The execution bot is separate from the SwiftChart analysis engine.

Flow:

```text
SwiftChart Analysis Engine -> Signal Webhook/API -> Telegram Trading Bot -> Risk Engine -> Exchange/Wallet
```

It does not contain or rewrite SwiftChart strategy logic. It only receives already-created signals, validates them, manages risk, and creates paper/live execution plans.

Telegram is the control surface for the execution service. The bot is admin-only, can pause/resume/kill trading, reports balance/open trades/PnL, and sends trade alerts whenever the signal webhook accepts or rejects a signal.

## Safety Defaults

- Default mode is `paper`.
- Live trading requires both:
  - `EXECUTION_MODE=live`
  - `EXECUTION_LIVE_CONFIRM=true`
- API keys and wallet keys must stay in `.env`.
- Hyperliquid live signing is intentionally stubbed until wallet credentials and signing are configured deliberately.

## Environment

Add these to `/opt/swiftchart/.env`:

```bash
EXECUTION_DATABASE_URL=sqlite:////opt/swiftchart/execution_bot.db
EXECUTION_WEBHOOK_SECRET=change-this-secret-with-at-least-32-random-chars
EXECUTION_CORS_ORIGINS=
EXECUTION_AUTH_CLOCK_SKEW_SECONDS=300
EXECUTION_NONCE_TTL_SECONDS=900
EXECUTION_AUTOTRADE_ENABLED=false
EXECUTION_SIGNAL_WEBHOOK_URL=http://127.0.0.1:8100/webhook/signal
EXECUTION_MODE=paper
EXECUTION_LIVE_CONFIRM=false
LIVE_TRADING=false
AUTO_EXECUTE=false
EXECUTION_EXCHANGE=mock
EXECUTION_TELEGRAM_BOT_TOKEN=your_execution_botfather_token
EXECUTION_TELEGRAM_ADMIN_ID=123456789
EXECUTION_TELEGRAM_POLLING_ENABLED=true
STARTING_BALANCE=100
TARGET_BALANCE=1000
BASE_RISK_PERCENT=2
MAX_RISK_PERCENT=5
MAX_DAILY_LOSS_PERCENT=8
MAX_WEEKLY_LOSS_PERCENT=15
MAX_OPEN_TRADES=2
MAX_LEVERAGE=5
MAX_CONSECUTIVE_LOSSES=3
MIN_CONFIDENCE_TO_TRADE=75
MIN_ORDER_NOTIONAL=10
MIN_PERP_VOLUME_24H=100000
EXECUTION_SYMBOL_ALLOWLIST=BTCUSDT,ETHUSDT,SOLUSDT
MAX_ENTRY_DEVIATION_PERCENT=0.75
MAX_SIGNAL_CANDLE_AGE_SECONDS=900
MAX_RISK_PER_TRADE_PERCENT=5
CIRCUIT_BREAKER_MAX_FAILURES=3
CIRCUIT_BREAKER_WINDOW_SECONDS=900
```

Execution webhooks and control routes require HMAC headers: `X-SwiftChart-Timestamp`, `X-SwiftChart-Nonce`, and `X-SwiftChart-Signature`. The signature is `HMAC_SHA256(EXECUTION_WEBHOOK_SECRET, "{timestamp}.{nonce}." + raw_body)`. Use `EXECUTION_EXCHANGE=hyperliquid` when you want market data from Hyperliquid.

For Hyperliquid API wallet credentials, prefer:

```bash
HYPERLIQUID_API_WALLET_ADDRESS=0xYOUR_API_WALLET_ADDRESS
HYPERLIQUID_API_KEY=your_api_key_or_api_wallet_secret
```

If your provider gives a separate API secret, use:

```bash
HYPERLIQUID_API_SECRET=your_api_secret
```

Do not paste these values into chat.

Live order submission is gated by both values below:

```bash
EXECUTION_MODE=live
EXECUTION_LIVE_CONFIRM=true
```

With both enabled, the bot submits Hyperliquid market orders through the official Python SDK and records the calculated stop loss and TP1/TP2/TP3 levels. Exchange-native trigger orders are not enabled yet, so monitor early live trades closely and keep balances small.

`EXECUTION_TELEGRAM_ADMIN_ID` is required. Messages from every other Telegram user are ignored and logged.

If the main SwiftChart alert bot already uses `TELEGRAM_BOT_TOKEN`, leave that value alone. Use `EXECUTION_TELEGRAM_BOT_TOKEN` for this separate trading-control bot.

## Run Locally

```bash
python3 -m venv execution_bot/.venv
execution_bot/.venv/bin/pip install -r execution_bot/requirements.txt
execution_bot/.venv/bin/uvicorn execution_bot.main:app --host 127.0.0.1 --port 8100
```

## Send A Test Signal

```bash
curl -X POST http://127.0.0.1:8100/webhook/signal \
  -H "Content-Type: application/json" \
  -H "X-SwiftChart-Secret: change-this-secret" \
  -d '{
    "pair": "BTC",
    "side": "BUY",
    "entry": 94500,
    "confidence": 87,
    "timeframe": "15m",
    "reason": "SwiftChart signal"
  }'
```

On the VPS through Nginx:

```bash
curl -X POST http://156.67.30.173/execution/webhook/signal \
  -H "Content-Type: application/json" \
  -H "X-SwiftChart-Secret: change-this-secret" \
  -d '{"pair":"BTC","side":"BUY","entry":94500,"confidence":87,"timeframe":"15m","reason":"SwiftChart signal"}'
```

## Dashboard

Frontend page:

```text
SwiftChart -> Execution
```

API:

```bash
curl http://127.0.0.1:8100/dashboard
curl http://156.67.30.173/execution/dashboard
```

## PM2

```bash
pm2 status
pm2 logs swiftchart-executor --lines 80
pm2 restart swiftchart-executor --update-env
```

## Controls

```bash
curl -X POST http://127.0.0.1:8100/pause
curl -X POST http://127.0.0.1:8100/resume
curl -X POST http://127.0.0.1:8100/kill
```

Telegram commands:

```text
/start
/help
/status
/balance
/positions
/open_trades
/closed_trades
/winrate
/pnl
/pause
/resume
/kill
/mode
/risk
/setrisk 2.5
/dashboard
```

`/setrisk` changes the runtime base risk percentage inside the execution database. It cannot exceed `MAX_RISK_PERCENT`; live mode requires `EXECUTION_MODE=live` and `EXECUTION_LIVE_CONFIRM=true`, or the explicit `LIVE_TRADING=true` override.

## Alerts

When `EXECUTION_AUTOTRADE_ENABLED=true` and `EXECUTION_SIGNAL_WEBHOOK_URL` is set, the SwiftChart background scanner forwards ranked trade ideas to `/webhook/signal`.

When a SwiftChart signal reaches `/webhook/signal`, the execution bot:

1. Validates the signal schema.
2. Checks kill/pause state, duplicate signals, confidence, open trades, daily/weekly loss limits, and cooldown/loss streak rules.
3. Fetches market data from the selected exchange adapter.
4. Checks volatility, spread, volume, ATR, and choppy-market filters.
5. Builds a paper/live execution plan with 1R/2R/3R targets.
6. Places the order through the selected exchange adapter.
7. Verifies the entry fill, exchange-native stop loss order, and exchange-native TP orders.
8. Syncs the live Hyperliquid account balance and stores execution events.
9. Sends Telegram alerts for opened trades, rejected signals, execution errors, and trade closure events.

Accepted trade alerts include pair, side, entry, stop loss, TP1/TP2/TP3, leverage, position size, confidence, market condition, balance, mode, and status.

## Risk Rules

- Rejects missing, duplicated, expired, and low-confidence signals.
- Stops after daily and weekly loss limits.
- Caps max open trades.
- Reduces risk by 50% after 3 losses in a row.
- Pauses new entries after 5 losses in a row.
- Uses ATR 14 plus recent market structure for stops.
- Uses 1R, 2R, and 3R targets with 40%, 30%, and 30% partial exits.
- TP1/TP2/TP3 plans are stored with every trade. TP1 is the breakeven trigger, TP2 is the trailing-stop trigger, and the exchange adapter remains the boundary for future live position management.
- Caps leverage and reduces size if leverage/exposure would be too high.

## Tests

```bash
execution_bot/.venv/bin/pytest execution_bot/tests
```
