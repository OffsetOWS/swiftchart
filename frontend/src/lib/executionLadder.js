function finiteNumber(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function clamp(value, minimum = 0, maximum = 100) {
  return Math.min(maximum, Math.max(minimum, value));
}

function percent(value, minimum, maximum) {
  if (value === null || minimum === null || maximum === null || maximum === minimum) return 50;
  return clamp(((value - minimum) / (maximum - minimum)) * 100);
}

export function executionLadderState(signal) {
  const direction = String(signal?.direction || "").toUpperCase();
  const isShort = direction === "SHORT" || direction === "SELL";
  const status = String(signal?.status || "PENDING_ENTRY").toUpperCase();
  const entryLow = finiteNumber(signal?.entryLow ?? signal?.entry_low);
  const entryHigh = finiteNumber(signal?.entryHigh ?? signal?.entry_high);
  const plannedEntry = entryLow !== null && entryHigh !== null
    ? (entryLow + entryHigh) / 2
    : finiteNumber(signal?.entry_price);
  const activatedEntry = finiteNumber(signal?.activatedEntryPrice ?? signal?.activated_entry_price);
  const entry = activatedEntry ?? plannedEntry;
  const stop = finiteNumber(signal?.stopLoss ?? signal?.stop_loss ?? signal?.sl);
  const tp1 = finiteNumber(signal?.takeProfit1 ?? signal?.take_profit_1 ?? signal?.tp);
  const tp2 = finiteNumber(signal?.takeProfit2 ?? signal?.take_profit_2 ?? signal?.tp2) ?? tp1;
  const storedPrice = finiteNumber(
    signal?.latestPrice
      ?? signal?.latest_price
      ?? signal?.lastMarketPrice
      ?? signal?.last_market_price,
  );
  const terminalPrice = status === "TP2_HIT" ? tp2 : status === "STOPPED" ? stop : storedPrice;
  const minimum = stop === null || tp2 === null ? null : Math.min(stop, tp2);
  const maximum = stop === null || tp2 === null ? null : Math.max(stop, tp2);
  const markerPosition = percent(terminalPrice, minimum, maximum);
  const entryStart = percent(entryLow ?? entry, minimum, maximum);
  const entryEnd = percent(entryHigh ?? entry, minimum, maximum);
  const tp1Position = percent(tp1, minimum, maximum);
  const tp2Position = percent(tp2, minimum, maximum);
  const stopPosition = percent(stop, minimum, maximum);
  const insideEntry = storedPrice !== null
    && entryLow !== null
    && entryHigh !== null
    && storedPrice >= Math.min(entryLow, entryHigh)
    && storedPrice <= Math.max(entryLow, entryHigh);

  let label = "Waiting for entry";
  let tone = "neutral";
  if (status === "TP1_HIT") {
    label = "TP1 reached";
    tone = "profit";
  } else if (status === "TP2_HIT") {
    label = "TP2 reached";
    tone = "profit";
  } else if (status === "STOPPED") {
    label = "Stop loss hit";
    tone = "loss";
  } else if (status === "EXPIRED") {
    label = "Signal expired";
    tone = "disabled";
  } else if (status === "CANCELLED") {
    label = "Signal cancelled";
    tone = "disabled";
  } else if (insideEntry && activatedEntry === null) {
    label = "At entry";
  } else if (status === "OPEN") {
    if (storedPrice === null || entry === null) {
      label = "Near entry";
    } else {
      const favorableDistance = isShort ? entry - storedPrice : storedPrice - entry;
      const neutralBand = Math.max(Math.abs((entryHigh ?? entry) - (entryLow ?? entry)), Math.abs(entry) * 0.0001);
      if (Math.abs(favorableDistance) <= neutralBand) label = "Near entry";
      else if (favorableDistance > 0) {
        label = "In profit";
        tone = "profit";
      } else {
        label = "In loss";
        tone = "loss";
      }
    }
  }

  const activeTarget = status === "TP1_HIT" ? tp2 : tp1;
  const favorableDistance = entry === null || storedPrice === null
    ? 0
    : isShort ? entry - storedPrice : storedPrice - entry;
  const targetDistance = entry === null || activeTarget === null
    ? 0
    : Math.abs(activeTarget - entry);

  return {
    status,
    label,
    tone,
    currentPrice: terminalPrice,
    markerPosition,
    entryStart: Math.min(entryStart, entryEnd),
    entryEnd: Math.max(entryStart, entryEnd),
    tp1Position,
    tp2Position,
    stopPosition,
    favorableProgress: targetDistance > 0 ? clamp((favorableDistance / targetDistance) * 100, -100, 100) : 0,
    tp1Complete: ["TP1_HIT", "TP2_HIT"].includes(status),
    tp2Complete: status === "TP2_HIT",
    stopComplete: status === "STOPPED",
    disabled: ["EXPIRED", "CANCELLED"].includes(status),
  };
}
