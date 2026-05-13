import { isSupabaseConfigured, supabase } from "./supabase.js";

function ensureSupabase() {
  if (!isSupabaseConfigured || !supabase) {
    throw new Error("Supabase is not configured yet.");
  }
}

function numberOrNull(value) {
  if (value === undefined || value === null || value === "") return null;
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : null;
}

function isMissingTable(error) {
  return error?.code === "42P01" || error?.code === "PGRST205" || String(error?.message || "").includes("paper_trades");
}

function paperTradeSetupError(error) {
  if (isMissingTable(error)) {
    return new Error("Supabase paper_trades table is not set up yet. Run supabase/paper_trades.sql in the Supabase SQL Editor.");
  }
  return new Error(error?.message || "Paper trade request failed.");
}

export function signalIdForIdea(idea) {
  const entry = Array.isArray(idea.entry_zone) ? idea.entry_zone.join("-") : "";
  return [
    idea.exchange || "hyperliquid",
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
    user_id: userId,
    signal_id: signalIdForIdea(idea),
    symbol: idea.symbol,
    exchange: idea.exchange || "hyperliquid",
    timeframe: idea.timeframe || null,
    direction: String(idea.direction || "").toLowerCase(),
    entry_price: numberOrNull(entry),
    stop_loss: numberOrNull(idea.stop_loss),
    take_profit: numberOrNull(idea.take_profit_1),
    take_profit_2: numberOrNull(idea.take_profit_2),
    risk_reward: numberOrNull(idea.risk_reward_ratio),
    confidence: numberOrNull(idea.confidence_score ?? idea.setup_score),
    market_bias: idea.regime_bias || idea.regime_label || idea.market_regime || idea.trend_alignment || null,
    status: "open",
    pnl: null,
    result: "open",
    source: "signal",
    paper_trade: true,
  };
}

export async function createPaperTradeFromSignal(idea, userId) {
  ensureSupabase();
  const payload = ideaToPaperTrade(idea, userId);
  const { data, error } = await supabase.from("paper_trades").insert(payload).select("*").single();
  if (error) {
    if (error.code === "23505") {
      return getPaperTradeBySignal(userId, payload.signal_id);
    }
    throw paperTradeSetupError(error);
  }
  return data;
}

export async function getPaperTradeBySignal(userId, signalId) {
  ensureSupabase();
  const { data, error } = await supabase
    .from("paper_trades")
    .select("*")
    .eq("user_id", userId)
    .eq("signal_id", signalId)
    .maybeSingle();
  if (error) throw paperTradeSetupError(error);
  return data;
}

export async function listPaperTrades(userId) {
  ensureSupabase();
  const { data, error } = await supabase
    .from("paper_trades")
    .select("*")
    .eq("user_id", userId)
    .order("created_at", { ascending: false });
  if (error) throw paperTradeSetupError(error);
  return data || [];
}

export async function listPaperTradesForSignals(userId, signalIds) {
  ensureSupabase();
  if (!signalIds.length) return [];
  const { data, error } = await supabase
    .from("paper_trades")
    .select("id, signal_id, status")
    .eq("user_id", userId)
    .in("signal_id", signalIds);
  if (error) {
    if (isMissingTable(error)) return [];
    throw paperTradeSetupError(error);
  }
  return data || [];
}

export async function updatePaperTradeStatus(tradeId, updates) {
  ensureSupabase();
  const { data, error } = await supabase.from("paper_trades").update(updates).eq("id", tradeId).select("*").single();
  if (error) throw paperTradeSetupError(error);
  return data;
}
