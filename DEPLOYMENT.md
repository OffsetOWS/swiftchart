# SwiftChart VPS Deployment

This guide replaces the old Render bot workflow with a Contabo Ubuntu VPS workflow.

The VPS runs:

- React/Vite frontend, built into `frontend/dist` and served by Nginx.
- FastAPI backend on `127.0.0.1:8000`, managed by PM2.
- Telegram bot in polling mode, managed by PM2.
- One shared SQLite database at `/opt/swiftchart/swiftchart.db`.

## Project Setup Detected

Frontend:

```text
Framework: React + Vite
Install: cd frontend && npm install
Build: cd frontend && npm run build
Output: frontend/dist
```

Backend:

```text
Framework: FastAPI
Install: python3 -m venv backend/.venv && backend/.venv/bin/pip install -r backend/requirements.txt
Start: backend/.venv/bin/uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
Health check: /health
```

Telegram bot:

```text
Framework: python-telegram-bot
Install: python3 -m venv bot/.venv && bot/.venv/bin/pip install -r bot/requirements.txt
Start: bot/.venv/bin/python -m bot.main
Mode: polling
```

Database:

```text
Type: SQLite
VPS path: /opt/swiftchart/swiftchart.db
Shared by: backend + Telegram bot
```

## 1. Connect To VPS

Run this from your computer:

```bash
ssh root@156.67.30.173
```

## 2. Install Server Packages

Run this on the VPS:

```bash
apt update && apt upgrade -y
apt install -y git curl nginx python3 python3-venv python3-pip ufw
curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
apt install -y nodejs
npm install -g pm2
apt install -y certbot python3-certbot-nginx
```

What this does:

- Installs Git so the VPS can pull from GitHub.
- Installs Python for the backend and bot.
- Installs Node.js for the frontend build.
- Installs PM2 so SwiftChart stays online.
- Installs Nginx to serve the website.
- Installs Certbot for SSL.

## 3. Open Firewall Ports

Run this on the VPS:

```bash
ufw allow OpenSSH
ufw allow 'Nginx Full'
ufw --force enable
ufw status
```

## 4. Clone The Project

Run this on the VPS:

```bash
mkdir -p /opt
cd /opt
git clone https://github.com/OffsetOWS/swiftchart.git
cd /opt/swiftchart
```

If the repo already exists:

```bash
cd /opt/swiftchart
git pull
```

## 5. Create The VPS Environment File

Run this on the VPS:

```bash
cd /opt/swiftchart
cp .env.example .env
nano .env
```

Use this template:

```env
VITE_API_BASE=
APP_NAME=SwiftChart
ENVIRONMENT=production
DATABASE_URL=sqlite:////opt/swiftchart/swiftchart.db
BINANCE_BASE_URL=https://api.binance.com
HYPERLIQUID_BASE_URL=https://api.hyperliquid.xyz
FRONTEND_ORIGINS=http://156.67.30.173,https://YOUR_DOMAIN
LIVE_TRADING_ENABLED=false
DEFAULT_EXCHANGE=hyperliquid
DEFAULT_TIMEFRAME=4h
DEFAULT_ACCOUNT_SIZE=10000
DEFAULT_RISK_PER_TRADE=1
DEFAULT_MIN_RR=2
DEFAULT_MAX_OPEN_TRADES=3
TRADE_HISTORY_EXPIRY_BARS=12
TELEGRAM_BOT_TOKEN=PASTE_YOUR_BOT_TOKEN_HERE
TELEGRAM_ALERT_CHAT_IDS=
ALERTS_ENABLED=true
ALERT_EXCHANGE=hyperliquid
ALERT_TIMEFRAME=4h
ALERT_SCAN_INTERVAL_SECONDS=1800
ALERTS_RUN_SECRET=CHANGE_THIS_RANDOM_SECRET
BOT_STATE_PATH=/opt/swiftchart/.swiftchart_bot_state.json
BINANCE_API_KEY=
BINANCE_API_SECRET=
HYPERLIQUID_API_KEY=
```

If you do not have a domain yet, keep `YOUR_DOMAIN` as a placeholder and use the IP address first.

## 6. Stop Telegram From Using Render Webhook

Replace `PASTE_YOUR_BOT_TOKEN_HERE` with your real bot token:

```bash
curl "https://api.telegram.org/botPASTE_YOUR_BOT_TOKEN_HERE/deleteWebhook?drop_pending_updates=true"
```

Then pause or delete the old Render service.

## 7. Deploy SwiftChart

Run this on the VPS:

```bash
cd /opt/swiftchart
bash deploy.sh
```

This command installs dependencies, builds the frontend, starts the API and bot with PM2, and configures Nginx.

## 8. Exact PM2 Command

If you only want to start or restart the app with PM2:

```bash
cd /opt/swiftchart
pm2 startOrReload ecosystem.config.js
pm2 save
```

Make PM2 restart automatically after VPS reboot:

```bash
pm2 startup systemd -u root --hp /root
```

PM2 will print one command. Copy and run the printed command.

## 9. Exact Nginx Config

Create the config:

```bash
nano /etc/nginx/sites-available/swiftchart
```

Paste this:

```nginx
server {
    listen 80;
    server_name 156.67.30.173 YOUR_DOMAIN;

    root /opt/swiftchart/frontend/dist;
    index index.html;

    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location /health {
        proxy_pass http://127.0.0.1:8000/health;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location / {
        try_files $uri $uri/ /index.html;
    }
}
```

Enable it:

```bash
ln -sf /etc/nginx/sites-available/swiftchart /etc/nginx/sites-enabled/swiftchart
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx
```

## 10. SSL With Certbot

SSL requires a real domain pointed at the VPS IP.

In your domain DNS, create this record:

```text
Type: A
Name: @ or app
Value: 156.67.30.173
```

Then run this on the VPS, replacing `YOUR_DOMAIN`:

```bash
certbot --nginx -d YOUR_DOMAIN
```

Check auto-renew:

```bash
certbot renew --dry-run
```

## 11. Update The App After New GitHub Code

Run this on the VPS whenever you push new code to GitHub:

```bash
cd /opt/swiftchart
git pull
bash deploy.sh
```

## 12. Check If SwiftChart Is Running

Check PM2:

```bash
pm2 status
```

Check backend:

```bash
curl http://127.0.0.1:8000/health
```

Check website from the VPS:

```bash
curl http://156.67.30.173
```

Check Nginx:

```bash
systemctl status nginx
```

Check logs:

```bash
pm2 logs swiftchart-api
pm2 logs swiftchart-bot
tail -f /var/log/nginx/error.log
```

Restart commands:

```bash
pm2 restart swiftchart-api
pm2 restart swiftchart-bot
systemctl restart nginx
```

## 13. Important Notes

- Do not commit `.env`.
- Do not paste API keys into GitHub.
- Render is no longer needed for the Telegram bot.
- The website and Telegram bot share the same SQLite DB because both use `DATABASE_URL=sqlite:////opt/swiftchart/swiftchart.db`.
- The bot uses Telegram polling on VPS, so it does not need `TELEGRAM_WEBHOOK_URL`.
