const API_BASE = import.meta.env.VITE_API_BASE || (import.meta.env.PROD ? "" : "http://localhost:8000");

function friendlyErrorMessage(message) {
  const text = String(message || "").trim();
  if (!text) return "Request failed. Please try again.";
  if (/api\.hyperliquid\.xyz|Internal Server Error|Server error '500'|HTTPStatusError/i.test(text)) {
    return "Hyperliquid market data is temporarily unavailable. Please try again shortly.";
  }
  if (/Failed to fetch|NetworkError|Load failed/i.test(text)) {
    return "Could not reach SwiftChart market data. Please check the backend and try again.";
  }
  return text;
}

async function request(path, options) {
  try {
    const response = await fetch(`${API_BASE}${path}`, {
      headers: { "Content-Type": "application/json" },
      ...options,
    });
    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: response.statusText }));
      throw new Error(friendlyErrorMessage(error.detail || response.statusText || "Request failed"));
    }
    return response.json();
  } catch (error) {
    throw new Error(friendlyErrorMessage(error.message));
  }
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

export function getTradeHistory(filters = {}) {
  const params = new URLSearchParams();
  Object.entries(filters).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== "") {
      params.set(key, value);
    }
  });
  const query = params.toString();
  return request(`/api/trade-history${query ? `?${query}` : ""}`);
}

export function getTradeHistoryDetail(id) {
  return request(`/api/trade-history/${id}`);
}

export function checkTradeHistory() {
  return request("/api/trade-history/check", { method: "POST" });
}

export function getTradeStats() {
  return request("/api/trade-stats");
}
