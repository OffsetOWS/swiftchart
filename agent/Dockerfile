FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app/agent \
    HOME=/tmp/hermes HERMES_HOME=/tmp/hermes TRADING_AGENT_HERMES_HOME=/tmp/hermes \
    TRADING_AGENT_STATE_PATH=/state/sessions TRADING_AGENT_LOG_PATH=/state/logs/agent.log
WORKDIR /app
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt \
    && useradd --uid 10001 --no-create-home agent \
    && mkdir -p /state/sessions /state/logs && chown -R 10001:10001 /state
COPY trading_agent /app/agent/trading_agent
COPY telegram_bot /app/agent/telegram_bot
USER 10001:10001
CMD ["python", "-m", "telegram_bot.main"]
