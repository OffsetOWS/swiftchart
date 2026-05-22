const FRESH_MS = 2 * 60 * 60 * 1000;
const AGING_MS = 8 * 60 * 60 * 1000;
const STALE_MS = 24 * 60 * 60 * 1000;

function numberOrNull(value) {
  if (value === undefined || value === null || value === "") return null;
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : null;
}

export function signalTimestamp(idea) {
  return idea.signal_timestamp || idea.signal_candle_time || idea.regime_updated_at || idea.created_at || null;
}

export function signalAgeMs(idea, now = Date.now()) {
  const timestamp = signalTimestamp(idea);
  if (!timestamp) return null;
  const parsed = new Date(timestamp).getTime();
  return Number.isFinite(parsed) ? Math.max(0, now - parsed) : null;
}

export function freshnessForIdea(idea, now = Date.now()) {
  const age = signalAgeMs(idea, now);
  const entryZone = Array.isArray(idea.entry_zone) ? idea.entry_zone : [];
  const entryMid = entryZone.length >= 2 ? (Number(entryZone[0]) + Number(entryZone[1])) / 2 : numberOrNull(idea.entry_price);
  const currentPrice = numberOrNull(idea.current_price || idea.mark_price || idea.price);
  const deviationPct = entryMid && currentPrice ? Math.abs(currentPrice - entryMid) / entryMid * 100 : 0;
  const movedTooFar = deviationPct >= 1.5;

  if (age === null) {
    return {
      status: "aging",
      label: "Aging",
      detail: "No candle timestamp",
      stale: false,
      ageMs: null,
      deviationPct,
    };
  }
  if (age > STALE_MS || movedTooFar) {
    return {
      status: "stale",
      label: "Stale",
      detail: movedTooFar ? "Price moved from entry" : "Signal is too old",
      stale: true,
      ageMs: age,
      deviationPct,
    };
  }
  if (age > AGING_MS) {
    return { status: "aging", label: "Aging", detail: "Needs refresh soon", stale: false, ageMs: age, deviationPct };
  }
  if (age <= FRESH_MS) {
    return { status: "fresh", label: "Fresh", detail: "Recent candle", stale: false, ageMs: age, deviationPct };
  }
  return { status: "aging", label: "Aging", detail: "Still usable", stale: false, ageMs: age, deviationPct };
}

export function liquidityForIdea(idea) {
  const volume = numberOrNull(idea.perpVolume24h || idea.perp_volume_24h || idea.volume_24h || idea.volume24h);
  const spreadPct = numberOrNull(idea.spread_percent || idea.spreadPct || idea.spread);
  if ((volume !== null && volume < 100000) || (spreadPct !== null && spreadPct > 0.35)) {
    return { status: "low", label: "Low Liquidity Warning", detail: volume ? `$${Math.round(volume).toLocaleString()} 24h perp volume` : "Thin or wide market", blocking: true };
  }
  if ((volume !== null && volume < 1000000) || (spreadPct !== null && spreadPct > 0.15)) {
    return { status: "medium", label: "Medium Liquidity", detail: volume ? `$${Math.round(volume).toLocaleString()} 24h perp volume` : "Moderate spread", blocking: false };
  }
  if (volume !== null) {
    return { status: "healthy", label: "Healthy Liquidity", detail: `$${Math.round(volume).toLocaleString()} 24h perp volume`, blocking: false };
  }
  return { status: "medium", label: "Medium Liquidity", detail: "Liquidity data limited", blocking: false };
}

export function regimeViewForIdea(idea) {
  const rawLabel = String(idea.regime_label || idea.market_regime || "");
  const type = String(idea.regime_type || idea.market_regime || "").toUpperCase();
  const score = numberOrNull(idea.regime_score);
  const confidence = numberOrNull(idea.regime_confidence_score);

  let label = rawLabel || "Range Environment";
  if (/TRENDING_UP|BREAKOUT|BULL/i.test(type + rawLabel)) label = score !== null && score < 35 ? "Weak Bull Trend" : "Strong Bull Trend";
  if (/TRENDING_DOWN|BREAKDOWN|BEAR/i.test(type + rawLabel)) label = score !== null && score > -35 ? "Breakdown Environment" : "Strong Bear Trend";
  if (/RANGE|NEUTRAL/i.test(type + rawLabel)) label = "Range Environment";
  if (/CHOP|NO_TRADE/i.test(type + rawLabel)) label = "High Risk Chop";
  if (/VOLATILITY/i.test(type + rawLabel)) label = "Volatility Expansion";

  const trend = score === null ? "Neutral" : score > 12 ? "Bullish" : score < -12 ? "Bearish" : "Neutral";
  const tone = /Bear|Breakdown/i.test(label) ? "bearish" : /Bull|Breakout/i.test(label) ? "bullish" : /Chop|High Risk/i.test(label) ? "risk" : "range";
  return {
    label,
    confidence: confidence === null ? null : Math.round(confidence),
    trend,
    tone,
  };
}

export function confidenceLabelForIdea(idea) {
  const score = numberOrNull(idea.setup_score ?? idea.confidence_score) || 0;
  const entryStatus = String(idea.entry_status || "READY").toUpperCase();
  const maturity = String(idea.move_maturity || "");
  if (entryStatus === "WAIT_FOR_RETEST") return "Wait For Retest";
  if (score >= 82) return "High Confidence";
  if (score >= 70) return "Medium Confidence";
  if (/Early/i.test(maturity)) return "Early Reversal";
  return "Aggressive Setup";
}

export function statusForIdea(idea) {
  const freshness = freshnessForIdea(idea);
  const liquidity = liquidityForIdea(idea);
  if (freshness.stale) return "expired";
  if (liquidity.blocking) return "invalidated";
  return freshness.status === "aging" ? "aging" : "active";
}
