import { createPaperTrade, getPaperTrades } from "./api.js";

function numberOrNull(value) {
  if (value === undefined || value === null || value === "") return null;
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : null;
}

function notesForIdea(idea, userId) {
  return JSON.stringify({
    user_id: userId,
    signal_id: signalIdForIdea(idea),
    risk_reward: numberOrNull(idea.risk_reward_ratio),
    confidence: numberOrNull(idea.confidence_score ?? idea.setup_score),
    market_bias: idea.regime_bias || idea.regime_label || idea.market_regime || idea.trend_alignment || null,
    setup_score: numberOrNull(idea.setup_score),
    reason: idea.reason || null,
  });
}

function parseNotes(notes) {
  if (!notes) return {};
  try {
    return JSON.parse(notes);
  } catch {
    return {};
  }
}

function normalizeTrade(row) {
  const notes = parseNotes(row.notes);
  return {
    ...row,
    signal_id: notes.signal_id || null,
    entry_price: row.entry_price,
    take_profit: row.take_profit_1,
    risk_reward: notes.risk_reward ?? null,
    confidence: notes.confidence ?? notes.setup_score ?? null,
    market_bias: notes.market_bias ?? null,
    result: row.result || (row.status === "closed" ? "closed" : "open"),
    pnl: row.pnl ?? null,
  };
}

export function signalIdForIdea(idea) {
  const entry = Array.isArray(idea.entry_zone) ? idea.entry_zone.join("-") : "";
  return [
    idea.source || idea.exchange || "hyperliquid",
    idea.symbol,
    idea.timeframe,
    idea.direction,
    entry,
    idea.stop_loss,
    idea.take_profit_1,
    idea.take_profit_2,
    idea.setup_score ?? idea.confidence_score,
  ]
    .map((item) => String(item ?? "").toLowerCase().replace(/\s+/g, ""))
    .join("|");
}

export function ideaToPaperTrade(idea, userId) {
  const entryZone = Array.isArray(idea.entry_zone) ? idea.entry_zone : [idea.entry_price, idea.entry_price];
  const entry = (Number(entryZone[0]) + Number(entryZone[1])) / 2;
  return {
    symbol: idea.symbol,
    exchange: idea.exchange || "hyperliquid",
    timeframe: idea.timeframe || "4h",
    direction: idea.direction,
    entry_price: numberOrNull(entry),
    stop_loss: numberOrNull(idea.stop_loss),
    take_profit_1: numberOrNull(idea.take_profit_1),
    take_profit_2: numberOrNull(idea.take_profit_2),
    size: numberOrNull(idea.position_size_units) || 0,
    notes: notesForIdea(idea, userId),
  };
}

export async function createPaperTradeFromSignal(idea, userId) {
  const payload = ideaToPaperTrade(idea, userId);
  return normalizeTrade(await createPaperTrade(payload));
}

export async function listPaperTrades(_userId) {
  const trades = await getPaperTrades();
  return (trades || []).map(normalizeTrade);
}

export async function listPaperTradesForSignals(_userId, signalIds) {
  if (!signalIds.length) return [];
  const wanted = new Set(signalIds);
  const trades = await listPaperTrades(_userId);
  return trades.filter((trade) => trade.signal_id && wanted.has(trade.signal_id));
}

export async function updatePaperTradeStatus() {
  throw new Error("Paper trade status updates are handled by the backend trade history checker.");
}
