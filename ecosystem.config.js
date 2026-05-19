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
    {
      name: "swiftchart-executor",
      cwd: "/opt/swiftchart",
      script: "bash",
      args: "-lc 'set -a; source /opt/swiftchart/.env; set +a; exec ./execution_bot/.venv/bin/uvicorn execution_bot.main:app --host 127.0.0.1 --port 8100'",
      instances: 1,
      autorestart: true,
      max_memory_restart: "500M",
      env: {
        NODE_ENV: "production",
      },
    },
  ],
};
