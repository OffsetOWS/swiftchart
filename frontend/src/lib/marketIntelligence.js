export const EMPTY_MARKET_INTELLIGENCE = Object.freeze({
  available: false,
  source: "CoinMarketCap",
  assets: {},
  trending: [],
  top_gainers: [],
  top_losers: [],
  highest_quality: [],
});

export function baseAssetSymbol(symbol) {
  return String(symbol || "")
    .trim()
    .toUpperCase()
    .replace(/(USDT|USDC|USD|PERP)$/, "");
}

export function marketDataForSignal(marketIntelligence, signalSymbol) {
  const symbol = baseAssetSymbol(signalSymbol);
  return marketIntelligence?.assets?.[symbol] || null;
}

export function formatCompactUsd(value) {
  const amount = Number(value);
  if (!Number.isFinite(amount)) return "-";
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    notation: "compact",
    maximumFractionDigits: 1,
  }).format(amount);
}

export function formatUsdPrice(value) {
  const amount = Number(value);
  if (!Number.isFinite(amount) || amount <= 0) return "-";
  const maximumFractionDigits = amount >= 1000 ? 2 : amount >= 1 ? 4 : 8;
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits,
  }).format(amount);
}

export function formatChange(value) {
  const change = Number(value);
  if (!Number.isFinite(change)) return "-";
  return `${change > 0 ? "+" : ""}${change.toFixed(2)}%`;
}
