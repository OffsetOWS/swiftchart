export function forexZoneStatus(snapshot) {
  const active = snapshot?.bias === "BEARISH"
    ? snapshot.sell_zones?.[0]
    : snapshot?.buy_zones?.[0];
  if (!active) return "No active zone";
  if (["LONG", "SHORT"].includes(snapshot?.decision) && active.state === "CONFIRMED") {
    return `${snapshot.decision} confirmed`;
  }
  if (active.state === "CONFIRMED") return "Entry requirements not met";
  if (["TOUCHED", "RETESTING", "SWEPT", "RECLAIMED"].includes(active.state)) {
    return "Reaction detected";
  }
  if (active.state === "INVALIDATED") return "Setup invalidated";
  return `${snapshot?.bias === "BEARISH" ? "Sell" : "Buy"} zone — waiting`;
}
