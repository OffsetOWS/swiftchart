// Summarises recent BTC candles for the header ticker and the Home range bar.

function compactPrice(value) {
  if (!Number.isFinite(value)) return "—";
  if (value >= 10_000) return `${(value / 1000).toFixed(1)}k`;
  return value.toLocaleString("en-US", { maximumFractionDigits: 2 });
}

export function summariseBtcCandles(candles = [], { rangeBars = 72, changeBars = 24 } = {}) {
  const rows = (Array.isArray(candles) ? candles : [])
    .map((candle) => ({
      t: new Date(candle.timestamp).getTime(),
      high: Number(candle.high),
      low: Number(candle.low),
      close: Number(candle.close),
    }))
    .filter((candle) => Number.isFinite(candle.close) && Number.isFinite(candle.high) && Number.isFinite(candle.low))
    .sort((a, b) => a.t - b.t);
  if (rows.length < 2) return null;

  const last = rows[rows.length - 1];
  const changeBase = rows[Math.max(0, rows.length - 1 - changeBars)].close;
  const window = rows.slice(-rangeBars);
  const high = Math.max(...window.map((row) => row.high));
  const low = Math.min(...window.map((row) => row.low));
  const span = high - low;

  return {
    price: last.close,
    priceLabel: last.close.toLocaleString("en-US", { maximumFractionDigits: 0 }),
    changePct: changeBase ? ((last.close - changeBase) / changeBase) * 100 : null,
    rangeLow: low,
    rangeHigh: high,
    rangeLowLabel: compactPrice(low),
    rangeHighLabel: compactPrice(high),
    rangePosition: span > 0 ? Math.min(100, Math.max(0, ((last.close - low) / span) * 100)) : 50,
    rangeDays: Math.round(window.length / 24),
  };
}

export function formatChangePct(value) {
  if (value === null || value === undefined || !Number.isFinite(value)) return "";
  const rounded = Math.abs(value) < 0.05 ? 0 : value;
  return `${rounded > 0 ? "+" : rounded < 0 ? "−" : ""}${Math.abs(rounded).toFixed(1)}%`;
}
