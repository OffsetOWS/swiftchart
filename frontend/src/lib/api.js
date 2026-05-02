const API_BASE = import.meta.env.VITE_API_BASE || (import.meta.env.PROD ? "" : "http://localhost:8000");

async function request(path, options) {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(error.detail || "Request failed");
  }
  return response.json();
}

export function getCandles({ exchange, symbol, timeframe }) {
  return request(`/api/candles?exchange=${exchange}&symbol=${symbol}&timeframe=${timeframe}&limit=240`);
}

export function getAnalysis({ exchange, symbol, timeframe, risk }) {
  const params = new URLSearchParams({
    exchange,
    symbol,
    timeframe,
    account_size: risk.accountSize,
    risk_per_trade_pct: risk.riskPerTrade,
    min_rr: risk.minRR,
    max_open_trades: risk.maxOpenTrades,
  });
  return request(`/api/analyze?${params.toString()}`);
}

export function getTopIdeas({ exchange, timeframe }) {
  return request(`/api/top-ideas?exchange=${exchange}&timeframe=${timeframe}`);
}

export function createPaperTrade(payload) {
  return request("/api/paper-trade", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function getPaperTrades() {
  return request("/api/paper-trades");
}
