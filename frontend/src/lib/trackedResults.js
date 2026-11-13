export function trackedResultR(trade) {
  if (trade?.cost_status !== "MODELED" || trade.net_r_multiple == null) return null;
  const value = Number(trade.net_r_multiple);
  return Number.isFinite(value) ? value : null;
}

export function trackedResultTotal(rows) {
  const values = rows.map(trackedResultR).filter((value) => value !== null);
  return {
    total: values.length ? values.reduce((sum, value) => sum + value, 0) : null,
    sample: values.length,
    unknown: rows.length - values.length,
  };
}
