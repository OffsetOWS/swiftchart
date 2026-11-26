// Pure helpers for the crypto Home screen: market regime, scan recency and recent outcomes.

const CLOSED_RESULTS = new Set(["WIN", "PARTIAL_WIN", "PARTIAL_LOSS", "LOSS", "BREAK_EVEN"]);

export function regimeFromScanStats(scanStats) {
  const score = Number(scanStats?.global_regime_score);
  const breadth = Number(scanStats?.breadth_above_ma_pct);
  const hasBreadth = Number.isFinite(breadth);
  if (!Number.isFinite(score)) {
    return {
      key: "unknown",
      label: "Reading the market",
      summary: "The first scan of this session is still running. The regime appears once it finishes.",
      position: null,
      breadth: hasBreadth ? Math.round(breadth) : null,
    };
  }
  const position = Math.min(100, Math.max(0, (score + 100) / 2));
  const breadthText = hasBreadth ? ` ${Math.round(breadth)}% of scanned markets trade above their trend average.` : "";
  if (score >= 25) {
    return {
      key: "up",
      label: "Trending up",
      summary: `Buyers control the broader market, so SwiftChart favours longs on pullbacks.${breadthText}`,
      position,
      breadth: hasBreadth ? Math.round(breadth) : null,
    };
  }
  if (score <= -25) {
    return {
      key: "down",
      label: "Trending down",
      summary: `Sellers control the broader market, so SwiftChart favours shorts into resistance.${breadthText}`,
      position,
      breadth: hasBreadth ? Math.round(breadth) : null,
    };
  }
  return {
    key: "range",
    label: "Range-bound",
    summary: `No side has control. SwiftChart waits for sweeps at range edges and skips mid-range trades.${breadthText}`,
    position,
    breadth: hasBreadth ? Math.round(breadth) : null,
  };
}

export function timeAgo(value, now = Date.now()) {
  if (!value) return "";
  const time = new Date(value).getTime();
  if (!Number.isFinite(time)) return "";
  const minutes = Math.max(0, Math.round((now - time) / 60_000));
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

function outcomeEvent(record) {
  if (record.tp2_hit_at) return "TP2 hit";
  if (record.sl_hit_at && record.tp1_hit_at) return "Stopped after TP1";
  if (record.sl_hit_at) return "Stopped";
  if (record.tp1_hit_at) return "TP1 hit";
  if (record.result === "BREAK_EVEN") return "Break-even";
  return "Closed at time exit";
}

export function normalizeOutcomes(records = [], limit = 5) {
  return records
    .filter((record) => CLOSED_RESULTS.has(String(record?.result || "").toUpperCase()))
    .map((record) => {
      const rValue = Number(record.pnl_r_multiple ?? record.gross_r_multiple);
      const closedAt = record.closed_at || record.sl_hit_at || record.tp2_hit_at || record.tp1_hit_at || record.outcome_checked_at;
      const direction = /short|sell/i.test(String(record.direction || "")) ? "short" : "long";
      return {
        id: record.id,
        symbol: String(record.symbol || "").replace(/USDT$|USD$|-PERP$/i, ""),
        direction,
        event: outcomeEvent(record),
        r: Number.isFinite(rValue) ? rValue : null,
        closedAt,
      };
    })
    .sort((a, b) => new Date(b.closedAt || 0) - new Date(a.closedAt || 0))
    .slice(0, limit);
}

export function formatR(value) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return "—";
  const number = Number(value);
  const rounded = Math.abs(number) < 0.05 ? 0 : number;
  const sign = rounded > 0 ? "+" : rounded < 0 ? "−" : "";
  return `${sign}${Math.abs(rounded).toFixed(1)}R`;
}
