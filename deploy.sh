#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/opt/swiftchart"
DOMAIN="${SWIFTCHART_DOMAIN:-}"

cd "$APP_DIR"

echo "Updating code..."
if [ -d "$APP_DIR/.git" ]; then
  git pull
else
  echo "No Git checkout found at $APP_DIR; using the files already on the server."
fi

echo "Installing Python dependencies..."
python3 -m venv backend/.venv
backend/.venv/bin/pip install --upgrade pip
backend/.venv/bin/pip install -r backend/requirements.txt

python3 -m venv bot/.venv
bot/.venv/bin/pip install --upgrade pip
bot/.venv/bin/pip install -r bot/requirements.txt

echo "Installing frontend dependencies..."
cd "$APP_DIR/frontend"
npm install

echo "Building frontend..."
set -a
source "$APP_DIR/.env"
set +a
npm run build
cd "$APP_DIR"

echo "Starting SwiftChart with PM2..."
pm2 startOrReload ecosystem.config.js
pm2 save

echo "Writing Nginx config..."
SERVER_NAME="_"
if [ -n "$DOMAIN" ]; then
  SERVER_NAME="$DOMAIN"
fi

cat > /etc/nginx/sites-available/swiftchart <<NGINX
server {
    listen 80;
    server_name ${SERVER_NAME};

    root /opt/swiftchart/frontend/dist;
    index index.html;

    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }

    location /health {
        proxy_pass http://127.0.0.1:8000/health;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }

    location / {
        try_files \$uri \$uri/ /index.html;
    }
}
NGINX

ln -sf /etc/nginx/sites-available/swiftchart /etc/nginx/sites-enabled/swiftchart
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx

echo "Done."
echo "Open http://${SERVER_NAME}"
echo "If you have a domain pointed to this VPS, run:"
echo "certbot --nginx -d ${SERVER_NAME}"
