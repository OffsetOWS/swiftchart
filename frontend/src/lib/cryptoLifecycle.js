// Crypto lifecycle facts come from the tracker, never from the sign of R/PnL.
export function cryptoLifecycleStatus(trade) {
  const raw = String(trade?.lifecycle_status || trade?.status || "OPEN").toUpperCase();
  if (raw === "TP1_HIT") return "TP1_HIT_TP2_RUNNING";
  if (["SL_HIT", "PARTIAL_LOSS", "BREAK_EVEN", "CLOSED_AFTER_TP1"].includes(raw)) return "STOPPED";
  if (["TAKEN", "ACTIVE", "TP1_HIT_TP2_RUNNING"].includes(raw)) return raw === "TP1_HIT_TP2_RUNNING" ? raw : "OPEN";
  if (raw === "TP2_HIT" || raw === "TIME_EXIT") return raw;
  if (["AMBIGUOUS", "UNRESOLVED", "EXPIRED_POSITION", "EXPIRED_RUNNER"].includes(raw)) return "UNRESOLVED";
  return raw;
}
