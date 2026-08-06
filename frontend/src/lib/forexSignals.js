const TIMEFRAME_ALIASES = new Map([
  ["15M", "15M"],
  ["M15", "15M"],
  ["15MIN", "15M"],
  ["15MINUTES", "15M"],
  ["1H", "1H"],
  ["H1", "1H"],
  ["1HR", "1H"],
  ["1HOUR", "1H"],
  ["60M", "1H"],
  ["4H", "4H"],
  ["H4", "4H"],
  ["4HR", "4H"],
  ["4HOURS", "4H"],
  ["240M", "4H"],
  ["1D", "1D"],
  ["D", "1D"],
  ["D1", "1D"],
  ["DAILY", "1D"],
  ["1DAY", "1D"],
]);

export const FOREX_TIMEFRAMES = ["15M", "1H", "4H", "1D"];
export const ACTIVE_FOREX_STATUSES = new Set(["PENDING_ENTRY", "OPEN"]);

export function canonicalForexTimeframe(value, fallback = "1H") {
  const normalized = String(value || "")
    .trim()
    .toUpperCase()
    .replace(/[\s_-]+/g, "");
  return TIMEFRAME_ALIASES.get(normalized) || fallback;
}

export function filterForexSignalsByTimeframe(signals, timeframe) {
  const selected = canonicalForexTimeframe(timeframe);
  return (Array.isArray(signals) ? signals : []).filter(
    (signal) => canonicalForexTimeframe(signal?.timeframe || signal?.execution_timeframe) === selected,
  );
}

export function activeForexSignals(signals) {
  return (Array.isArray(signals) ? signals : []).filter(
    (signal) => ACTIVE_FOREX_STATUSES.has(String(signal?.status || "").toUpperCase()),
  );
}
