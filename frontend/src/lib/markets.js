export const MARKET_TYPES = Object.freeze({
  crypto: "crypto",
  forex: "forex",
});

export const MARKET_OPTIONS = Object.freeze([
  { key: MARKET_TYPES.crypto, label: "Crypto" },
  { key: MARKET_TYPES.forex, label: "Forex" },
]);

export function normalizeMarket(value) {
  return value === MARKET_TYPES.forex ? MARKET_TYPES.forex : MARKET_TYPES.crypto;
}

export function marketFromSearch(search) {
  return normalizeMarket(new URLSearchParams(search || "").get("market"));
}
