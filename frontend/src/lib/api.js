const configuredApiBase = import.meta.env.VITE_API_BASE || "";
const isLocalApiBase = /^https?:\/\/(localhost|127\.0\.0\.1|\[::1\])(?::\d+)?/i.test(configuredApiBase);
const API_BASE = configuredApiBase && (!isLocalApiBase || import.meta.env.DEV)
  ? configuredApiBase
  : import.meta.env.DEV
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
      const requestError = new Error(friendlyErrorMessage(detail || response.statusText || `Request failed (${response.status})`));
      requestError.status = response.status;
      requestError.kind = response.status === 401 || response.status === 403
        ? "authentication"
        : response.status >= 500
          ? "service"
          : "request";
      throw requestError;
    }
    return response.json();
  } catch (error) {
    if (error?.status) throw error;
    const requestError = new Error(friendlyErrorMessage(error?.message));
    requestError.kind = /Failed to fetch|NetworkError|Load failed/i.test(String(error?.message || ""))
      ? "network"
      : "request";
    throw requestError;
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

export function getMarketIntelligence(symbols = []) {
  const uniqueSymbols = [...new Set(symbols.filter(Boolean))];
  const query = uniqueSymbols.length ? `?symbols=${encodeURIComponent(uniqueSymbols.join(","))}` : "";
  return request(`/api/market-intelligence${query}`);
}

export function refreshTopIdeasCache({ exchange, timeframe }) {
  return request(`/api/top-ideas/refresh?exchange=${exchange}&timeframe=${timeframe}`, { method: "POST" });
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

export function getForexOverview() {
  return request("/api/forex/overview");
}

export function getForexSignals(timeframe = "") {
  const query = timeframe ? `?timeframe=${encodeURIComponent(timeframe)}` : "";
  return request(`/api/forex/signals${query}`);
}

export function getForexSignal(signalId) {
  return request(`/api/forex/signals/${encodeURIComponent(signalId)}`);
}

export function runForexScan(timeframe, accessToken) {
  return request(`/api/forex/scan?timeframe=${encodeURIComponent(timeframe)}`, {
    method: "POST",
    accessToken,
  });
}

export function takeForexTrade(signalId, payload, accessToken) {
  return request(`/api/forex/signals/${encodeURIComponent(signalId)}/take-trade`, {
    method: "POST",
    body: JSON.stringify(payload),
    accessToken,
  });
}

export function getForexSessions() {
  return request("/api/forex/sessions");
}

export function getForexPairs() {
  return request("/api/forex/pairs");
}

export function getMt5Status() {
  return request("/api/performance");
}

export function submitPaymentApi(payload, accessToken) {
  return request("/api/payments/submissions", {
    method: "POST",
    body: JSON.stringify(payload),
    accessToken,
  });
}

export function listMyPaymentsApi(accessToken) {
  return request("/api/payments/submissions/me", { accessToken });
}

export function paymentAdminAccessApi(accessToken) {
  return request("/api/payments/admin/access", { accessToken });
}

export function listPendingPaymentsApi(accessToken) {
  return request("/api/payments/admin/submissions", { accessToken });
}

export function reviewPaymentApi(submissionId, payload, accessToken) {
  return request(`/api/payments/admin/submissions/${submissionId}/review`, {
    method: "POST",
    body: JSON.stringify(payload),
    accessToken,
  });
}
