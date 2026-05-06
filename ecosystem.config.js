module.exports = {
  apps: [
    {
      name: "swiftchart-api",
      cwd: "/opt/swiftchart",
      script: "bash",
      args: "-lc 'set -a; source /opt/swiftchart/.env; set +a; exec ./backend/.venv/bin/uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000'",
      instances: 1,
      autorestart: true,
      max_memory_restart: "600M",
      env: {
        NODE_ENV: "production",
      },
    },
    {
      name: "swiftchart-bot",
      cwd: "/opt/swiftchart",
      script: "bash",
      args: "-lc 'set -a; source /opt/swiftchart/.env; set +a; exec ./bot/.venv/bin/python -m bot.main'",
      instances: 1,
      autorestart: true,
      max_memory_restart: "600M",
      env: {
        NODE_ENV: "production",
      },
    },
  ],
};
