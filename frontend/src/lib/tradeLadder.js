// Builds the vertical trade ladder for the signal detail screen.
// Rows are sorted by price (highest first) so the ladder reads like a chart.

function num(value) {
  if (value === null || value === undefined || value === "" || value === "-") return null;
  const number = Number(typeof value === "string" ? value.replace(/,/g, "") : value);
  return Number.isFinite(number) ? number : null;
}

function firstNum(...values) {
  for (const value of values) {
    const number = num(value);
    if (number !== null) return number;
  }
  return null;
}

export function formatLadderPrice(value) {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  const abs = Math.abs(value);
  const digits = abs >= 1000 ? 1 : abs >= 10 ? 2 : abs >= 1 ? 4 : 6;
  return value.toLocaleString("en-US", { minimumFractionDigits: 0, maximumFractionDigits: digits });
}

function formatPct(value) {
  if (value === null || !Number.isFinite(value)) return "";
  const rounded = Math.abs(value) < 0.005 ? 0 : value;
  return `${rounded > 0 ? "+" : rounded < 0 ? "−" : ""}${Math.abs(rounded).toFixed(2)}%`;
}

function formatRLabel(value) {
  if (value === null || !Number.isFinite(value)) return "";
  if (Math.abs(value) < 0.05) return "0R";
  return `${value > 0 ? "+" : "−"}${Math.abs(value).toFixed(1)}R`;
}

export function buildTradeLadder(signal, progress = {}) {
  const isShort = /short|sell/i.test(String(signal?.direction || ""));
  const entryLow = num(signal?.entryLow);
  const entryHigh = num(signal?.entryHigh);
  const entry = entryLow !== null && entryHigh !== null ? (entryLow + entryHigh) / 2 : entryLow ?? entryHigh;
  const raw = signal?.rawIdea || {};
  const stop = firstNum(raw.stop_loss, signal?.stopLoss, signal?.stop_loss, signal?.sl);
  const tp1 = firstNum(raw.take_profit_1, signal?.takeProfit1, signal?.take_profit_1, signal?.tp);
  const tp2Raw = firstNum(raw.take_profit_2, signal?.takeProfit2, signal?.take_profit_2, signal?.tp2);
  const tp2 = tp2Raw !== null && tp2Raw !== tp1 ? tp2Raw : null;
  const current = num(progress.currentPrice ?? signal?.latestPrice);
  const risk = entry !== null && stop !== null ? Math.abs(entry - stop) : null;

  const rOf = (price) => {
    if (price === null || entry === null || !risk) return null;
    return ((price - entry) / risk) * (isShort ? -1 : 1);
  };
  const distanceOf = (price) => (price === null || !current ? null : ((price - current) / current) * 100);

  const levels = [
    tp2 !== null && { key: "tp2", label: "TP2", price: tp2, tone: "target", done: Boolean(progress.tp2Complete) },
    tp1 !== null && { key: "tp1", label: "TP1", price: tp1, tone: "target", done: Boolean(progress.tp1Complete) },
    entry !== null && {
      key: "entry",
      label: "Entry zone",
      price: entry,
      display: entryLow !== null && entryHigh !== null && entryLow !== entryHigh
        ? `${formatLadderPrice(Math.min(entryLow, entryHigh))} – ${formatLadderPrice(Math.max(entryLow, entryHigh))}`
        : formatLadderPrice(entry),
      tone: "entry",
      done: false,
    },
    stop !== null && { key: "stop", label: "Stop loss", price: stop, tone: "stop", done: Boolean(progress.stopComplete) },
  ]
    .filter(Boolean)
    .map((level) => ({
      ...level,
      display: level.display || formatLadderPrice(level.price),
      r: level.key === "entry" ? "" : formatRLabel(rOf(level.price)),
      distance: level.done ? "" : formatPct(distanceOf(level.price)),
      inside: level.key === "entry" && current !== null && entryLow !== null && entryHigh !== null
        && current >= Math.min(entryLow, entryHigh) && current <= Math.max(entryLow, entryHigh),
    }));

  const rows = [...levels].sort((a, b) => b.price - a.price);
  if (current !== null && rows.length) {
    const nowRow = { key: "now", label: "Now", price: current, display: formatLadderPrice(current), tone: "now", r: formatRLabel(rOf(current)), distance: "" };
    let index = rows.findIndex((row) => current > row.price);
    if (index === -1) index = rows.length;
    rows.splice(index, 0, nowRow);
  }

  return {
    rows,
    isShort,
    currentDisplay: formatLadderPrice(current),
    currentR: formatRLabel(rOf(current)),
    hasLevels: levels.length >= 2,
  };
}

export function formatValidUntil(value, now = Date.now()) {
  if (!value) return null;
  const date = new Date(value);
  const time = date.getTime();
  if (!Number.isFinite(time)) return null;
  const hoursLeft = (time - now) / 3_600_000;
  const when = date.toLocaleString("en-GB", { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
  if (hoursLeft <= 0) return { when, left: "Window ended" };
  return { when, left: hoursLeft >= 1 ? `${Math.floor(hoursLeft)}h left` : `${Math.max(1, Math.round(hoursLeft * 60))}m left` };
}
