const configuredApiBase = import.meta.env.VITE_API_BASE || "";
const isLocalApiBase = /^https?:\/\/(localhost|127\.0\.0\.1|\[::1\])(?::\d+)?/i.test(configuredApiBase);
const isLocalApp =
  typeof window !== "undefined" &&
  ["localhost", "127.0.0.1", "::1"].includes(window.location.hostname);

const API_BASE = configuredApiBase && (!isLocalApiBase || isLocalApp)
  ? configuredApiBase
  : isLocalApp
    ? "http://127.0.0.1:8000"
    : "";

function friendlyErrorMessage(message) {
  const text = String(message || "").trim();
  if (!text) return "Request failed. Please try again.";
  if (/api\.hyperliquid\.xyz|Internal Server Error|Server error '500'|HTTPStatusError/i.test(text)) {
    return "Hyperliquid market data is temporarily unavailable. Please try again shortly.";
  }
  if (/FUNCTION_INVOCATION_TIMEOUT|Gateway Timeout|504/i.test(text)) {
    return "SwiftChart scanner took too long to finish. Please try again in a moment.";
  }
  if (/Failed to fetch|NetworkError|Load failed/i.test(text)) {
    return "Could not reach SwiftChart market data. Please check the backend and try again.";
  }
  return text;
}

async function request(path, options = {}) {
  const { accessToken, ...fetchOptions } = options;
  const headers = { "Content-Type": "application/json", ...(fetchOptions.headers || {}) };
  if (accessToken) {
    headers.Authorization = `Bearer ${accessToken}`;
  }
  try {
    const response = await fetch(`${API_BASE}${path}`, {
      ...fetchOptions,
      headers,
    });
    if (!response.ok) {
      const detail = await response.text().then((text) => {
        if (!text) return response.statusText;
        try {
          return JSON.parse(text).detail || text;
        } catch {
          return text;
        }
      });
      throw new Error(friendlyErrorMessage(detail || response.statusText || `Request failed (${response.status})`));
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

export function createPaperTrade(payload, accessToken) {
  return request("/api/paper-trade", {
    method: "POST",
    body: JSON.stringify(payload),
    accessToken,
  });
}

export function getPaperTrades(userId, accessToken) {
  const query = userId ? `?user_id=${encodeURIComponent(userId)}` : "";
  return request(`/api/paper-trades${query}`, { accessToken });
}

export function updatePaperTrade(id, payload, userId, accessToken) {
  const query = userId ? `?user_id=${encodeURIComponent(userId)}` : "";
  return request(`/api/paper-trades/${id}${query}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
    accessToken,
  });
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
