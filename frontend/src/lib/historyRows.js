// Turns saved paper trades into History rows with clean labels.
// The backend title-cases raw statuses ("Sl Hit", "Tp2 Hit") and can overwrite the
// label with "Costs unknown" when a refresh fails, so labels are derived from status here.

const STATUS = {
  PENDING_ENTRY: { label: "Waiting for entry", group: "open" },
  WAITING_FOR_ENTRY: { label: "Waiting for entry", group: "open" },
  OPEN: { label: "Open", group: "open" },
  ACTIVE: { label: "Open", group: "open" },
  TAKEN: { label: "Open", group: "open" },
  TP1_HIT: { label: "TP1 hit", group: "tp1" },
  TP1_HIT_TP2_RUNNING: { label: "TP1 hit · running", group: "tp1" },
  TP2_HIT: { label: "TP2 hit", group: "tp2" },
  SL_HIT: { label: "Stopped", group: "stopped" },
  STOPPED: { label: "Stopped", group: "stopped" },
  STOPPED_AFTER_TP1: { label: "Stopped after TP1", group: "stopped" },
  TIME_EXIT: { label: "Time exit", group: "expired" },
  EXPIRED: { label: "Expired", group: "expired" },
  EXPIRED_POSITION: { label: "Expired", group: "expired" },
  EXPIRED_RUNNER: { label: "Expired after TP1", group: "expired" },
  NO_ENTRY: { label: "No entry", group: "expired" },
  CANCELLED: { label: "Cancelled", group: "expired" },
  AMBIGUOUS: { label: "Unresolved", group: "expired" },
  UNRESOLVED: { label: "Unresolved", group: "expired" },
};

export const HISTORY_FILTERS = [
  ["all", "All"],
  ["open", "Open"],
  ["tp1", "TP1"],
  ["tp2", "TP2"],
  ["stopped", "Stopped"],
  ["expired", "Expired"],
];

function finite(value) {
  if (value === null || value === undefined || value === "") return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

export function historyStatus(trade = {}) {
  const lifecycle = String(trade.lifecycle_status || "").toUpperCase();
  if (STATUS[lifecycle]) return STATUS[lifecycle];
  const status = String(trade.status || "").toUpperCase();
  if (status === "TP_HIT") return STATUS.TP1_HIT_TP2_RUNNING;
  if (STATUS[status]) return STATUS[status];
  if (status === "CLOSED") {
    const result = String(trade.lifecycle_result || trade.result || "").toLowerCase();
    if (result === "win") return { label: "Closed in profit", group: "tp2" };
    if (result === "loss") return { label: "Closed at a loss", group: "stopped" };
    return { label: "Closed", group: "expired" };
  }
  return STATUS.OPEN;
}

export function historyResultR(trade = {}) {
  // Results are shown before fees and funding.
  return finite(trade.gross_r_multiple) ?? finite(trade.pnl_r_multiple) ?? finite(trade.result_r);
}

export function buildHistoryRow(trade = {}) {
  const status = historyStatus(trade);
  const symbol = String(trade.symbol || "");
  const match = symbol.match(/^(.+?)(USDT|USDC|USD)$/i);
  const taken = trade.taken_at ? new Date(trade.taken_at) : null;
  return {
    id: trade.id,
    base: match ? match[1] : symbol,
    quote: match ? match[2] : "",
    direction: /short|sell/i.test(String(trade.direction || "")) ? "short" : "long",
    timeframe: String(trade.timeframe || "").toUpperCase(),
    date: taken && Number.isFinite(taken.getTime()) ? taken.toLocaleDateString("en-GB", { day: "numeric", month: "short" }) : "",
    label: status.label,
    group: status.group,
    r: status.group === "open" ? null : historyResultR(trade),
    market: String(trade.exchange || "").toLowerCase() === "forex" ? "forex" : "crypto",
    trade,
  };
}
