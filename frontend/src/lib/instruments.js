const QUOTE_SUFFIXES = ["USDT", "USDC", "BUSD", "USD", "BTC", "ETH"];
const FOREX_CURRENCIES = new Set(["AUD", "CAD", "CHF", "EUR", "GBP", "JPY", "NZD", "USD", "XAG", "XAU"]);

export function cleanInstrumentSymbol(symbol) {
  return String(symbol || "").trim().toUpperCase().replace(/[^A-Z0-9]/g, "");
}

export function normalizeCryptoSymbol(symbol) {
  const raw = String(symbol || "").trim().toUpperCase();
  const separatedBase = raw.split(/[\/_-]/).filter(Boolean)[0];
  const clean = cleanInstrumentSymbol(separatedBase || raw).replace(/PERP$/, "");

  for (const suffix of QUOTE_SUFFIXES) {
    if (clean.length > suffix.length && clean.endsWith(suffix)) {
      return clean.slice(0, -suffix.length);
    }
  }
  return clean;
}

export function normalizeForexPair(symbol) {
  const raw = String(symbol || "").trim().toUpperCase();
  const separated = raw.split(/[\/_-]/).filter(Boolean);

  if (separated.length >= 2) {
    return {
      base: cleanInstrumentSymbol(separated[0]).slice(0, 3),
      quote: cleanInstrumentSymbol(separated[1]).slice(0, 3),
    };
  }

  const clean = cleanInstrumentSymbol(raw);
  return {
    base: clean.slice(0, 3),
    quote: clean.slice(3, 6),
  };
}

export function inferMarketType(symbol, marketType) {
  if (marketType === "crypto" || marketType === "forex") return marketType;
  const { base, quote } = normalizeForexPair(symbol);
  return base && quote && FOREX_CURRENCIES.has(base) && FOREX_CURRENCIES.has(quote) ? "forex" : "crypto";
}

export function instrumentInitials(symbol, marketType) {
  if (inferMarketType(symbol, marketType) === "forex") {
    const { base, quote } = normalizeForexPair(symbol);
    return `${base.slice(0, 2)} · ${quote.slice(0, 2)}`;
  }
  return normalizeCryptoSymbol(symbol).slice(0, 4) || "?";
}
