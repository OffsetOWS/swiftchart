import { createPaperTrade, getPaperTrade, getPaperTrades, updatePaperTrade } from "./api.js";
import { freshnessForIdea, liquidityForIdea, signalTimestamp, statusForIdea } from "./signalQuality.js";

function numberOrNull(value) {
  if (value === undefined || value === null || value === "") return null;
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : null;
}

function notesForIdea(idea, userId) {
  const freshness = freshnessForIdea(idea);
  const liquidity = liquidityForIdea(idea);
  return JSON.stringify({
    user_id: userId,
    signal_id: signalIdForIdea(idea),
    source_signal_id: idea.id || idea.public_id || null,
    risk_reward: numberOrNull(idea.risk_reward_ratio),
    confidence: numberOrNull(idea.confidence_score ?? idea.setup_score),
    market_bias: idea.regime_bias || idea.regime_label || idea.market_regime || idea.trend_alignment || null,
    market_regime: idea.regime_label || idea.market_regime || null,
    liquidity_status: liquidity.label,
    freshness_status: freshness.label,
    signal_timestamp: signalTimestamp(idea),
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
    direction: String(row.direction || "").toLowerCase(),
    signal_id: row.signal_id || notes.signal_id || null,
    source_signal_id: row.source_signal_id || notes.source_signal_id || null,
    entry_price: row.entry_price,
    take_profit: row.take_profit_1,
    risk_reward: notes.risk_reward ?? null,
    confidence: notes.confidence ?? notes.setup_score ?? null,
    market_bias: row.market_bias ?? row.market_regime ?? notes.market_bias ?? null,
    market_regime: row.market_regime ?? notes.market_regime ?? row.market_bias ?? null,
    liquidity_status: row.liquidity_status ?? notes.liquidity_status ?? null,
    signal_timestamp: row.signal_timestamp ?? notes.signal_timestamp ?? null,
    result: row.result || (row.status === "closed" ? "closed" : "open"),
    pnl: row.pnl ?? null,
    taken_at: row.taken_at || row.created_at,
  };
}

export function signalIdForIdea(idea) {
  const entry = Array.isArray(idea.entry_zone) ? idea.entry_zone.join("-") : "";
  const candleTime = signalTimestamp(idea) || "";
  return [
    idea.source || idea.exchange || "hyperliquid",
    idea.symbol,
    idea.timeframe,
    idea.direction,
    entry,
    candleTime,
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
  const liquidity = liquidityForIdea(idea);
  return {
    signal_id: signalIdForIdea(idea),
    user_id: userId,
    symbol: idea.symbol,
    exchange: idea.exchange || "hyperliquid",
    timeframe: idea.timeframe || "4h",
    direction: idea.direction,
    entry_price: numberOrNull(entry),
    stop_loss: numberOrNull(idea.stop_loss),
    take_profit_1: numberOrNull(idea.take_profit_1),
    take_profit_2: numberOrNull(idea.take_profit_2),
    size: numberOrNull(idea.position_size_units) || 0,
    risk_reward: numberOrNull(idea.risk_reward_ratio),
    setup_score: numberOrNull(idea.setup_score),
    confidence: numberOrNull(idea.confidence_score ?? idea.setup_score),
    market_bias: idea.regime_bias || idea.regime_label || idea.market_regime || idea.trend_alignment || null,
    market_regime: idea.regime_label || idea.market_regime || null,
    liquidity_status: liquidity.label,
    signal_timestamp: signalTimestamp(idea),
    status: statusForIdea(idea) === "active" ? "taken" : statusForIdea(idea),
    notes: notesForIdea(idea, userId),
  };
}

export async function createPaperTradeFromSignal(idea, userId, accessToken) {
  const payload = ideaToPaperTrade(idea, userId);
  return normalizeTrade(await createPaperTrade(payload, accessToken));
}

export async function listPaperTrades(_userId, accessToken) {
  const trades = await getPaperTrades(_userId, accessToken);
  return (trades || []).map(normalizeTrade);
}

export async function getPaperTradeDetail(id, userId, accessToken) {
  return normalizeTrade(await getPaperTrade(id, userId, accessToken));
}

export async function listPaperTradesForSignals(_userId, signalIds, accessToken) {
  if (!signalIds.length) return [];
  const wanted = new Set(signalIds);
  const trades = await listPaperTrades(_userId, accessToken);
  return trades.filter((trade) => trade.signal_id && wanted.has(trade.signal_id));
}

export async function updatePaperTradeStatus(id, statusUpdate, accessToken) {
  const userId = statusUpdate?.user_id || null;
  return normalizeTrade(await updatePaperTrade(id, statusUpdate, userId, accessToken));
}
