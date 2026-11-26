export const MARKET_TYPES = Object.freeze({
  crypto: "crypto",
  forex: "forex",
});

// Forex is paused. Set VITE_FOREX_ENABLED=true at build time to bring it back.
export const FOREX_ENABLED = String(import.meta.env?.VITE_FOREX_ENABLED || "").toLowerCase() === "true";

export const MARKET_OPTIONS = Object.freeze([
  { key: MARKET_TYPES.crypto, label: "Crypto" },
  ...(FOREX_ENABLED ? [{ key: MARKET_TYPES.forex, label: "Forex" }] : []),
]);

export function normalizeMarket(value) {
  return FOREX_ENABLED && value === MARKET_TYPES.forex ? MARKET_TYPES.forex : MARKET_TYPES.crypto;
}

export function marketFromSearch(search) {
  return normalizeMarket(new URLSearchParams(search || "").get("market"));
}
