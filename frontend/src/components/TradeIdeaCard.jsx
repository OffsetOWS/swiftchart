import { Activity, BrainCircuit, CheckCircle2, Clock3, Gauge, ShieldAlert, TrendingUp, Waves, Zap } from "lucide-react";
import { confidenceLabelForIdea, freshnessForIdea, liquidityForIdea, regimeViewForIdea, statusForIdea } from "../lib/signalQuality.js";

function fmt(value) {
  if (value === undefined || value === null) return "-";
  return Number(value).toLocaleString(undefined, { maximumFractionDigits: 6 });
}

function sourceLabel(value) {
  if (value === "variational") return "Variational";
  if (value === "hyperliquid") return "Hyperliquid";
  return value || "exchange";
}

export default function TradeIdeaCard({
  idea,
  onPaperTrade,
  tradeTaken = false,
  paperTradeLoading = false,
  onAiScan,
  aiResult,
  aiError = "",
  aiLoading = false,
}) {
  const directionClass = String(idea.direction || "").toLowerCase();
  const score = idea.setup_score ?? idea.confidence_score;
  const entryStatus = String(idea.entry_status || "READY").trim().toUpperCase();
  const freshness = freshnessForIdea(idea);
  const liquidity = liquidityForIdea(idea);
  const regime = regimeViewForIdea(idea);
  const signalStatus = statusForIdea(idea);
  const confidenceLabel = confidenceLabelForIdea(idea);
  const blockedStatus = ["WAIT_FOR_RETEST", "REJECTED_EXHAUSTED", "REJECTED"].includes(entryStatus);
  const canTakeTrade = !blockedStatus && !freshness.stale && !liquidity.blocking;
  const whyThisSetup = idea.reason || "Clean setup with defined entry, stop, and targets.";
  const trendLabel = idea.trend_alignment ? String(idea.trend_alignment).replace("-", " ") : regime.trend;
  const volatilityWarning = idea.exhaustion_risk === "High" || entryStatus === "WAIT_FOR_RETEST" || freshness.status === "aging";
  const takeTradeLabel = tradeTaken
    ? "Trade Taken"
    : paperTradeLoading
      ? "Saving..."
      : freshness.stale
        ? "Signal Stale"
        : liquidity.blocking
          ? "Low Liquidity"
          : entryStatus === "WAIT_FOR_RETEST"
            ? "Wait for Retest"
            : entryStatus === "REJECTED_EXHAUSTED"
              ? "Rejected"
              : "Take Trade";

  return (
    <article className={`idea-card signal-card ${directionClass} status-${signalStatus}`}>
      <div className="idea-top">
        <div className="signal-title-block">
          <span className="exchange-label">{sourceLabel(idea.source || idea.exchange)} · {idea.timeframe}</span>
          <h3>{idea.symbol}</h3>
          <div className="signal-chip-row">
            <span className={`regime-badge ${regime.tone}`}><Waves size={13} /> {regime.label}</span>
            <span className={`freshness-pill ${freshness.status}`}><Clock3 size={13} /> {freshness.label}</span>
            <span className={`liquidity-pill ${liquidity.status}`}><Gauge size={13} /> {liquidity.label}</span>
          </div>
        </div>
        <span className={`direction-badge ${directionClass}`}>
          <Activity size={15} /> {String(idea.direction || "").toUpperCase()}
        </span>
      </div>
      <div className="signal-score-row">
        <div>
          <span>{confidenceLabel}</span>
          <b>{Math.round(score || 0)}<small>/100</small></b>
        </div>
        <div>
          <span>Risk / Reward</span>
          <b>{idea.risk_reward_ratio}R</b>
        </div>
        <div>
          <span>Trend</span>
          <b>{trendLabel}</b>
        </div>
      </div>
      <div className="confidence-rail" aria-hidden="true">
        <span style={{ width: `${Math.min(100, Math.max(0, score))}%` }} />
      </div>
      <div className="trade-level-grid">
        <div className="metric"><span>Entry zone</span><b>{fmt(idea.entry_zone[0])} - {fmt(idea.entry_zone[1])}</b></div>
        <div className="metric"><span>Stop loss</span><b>{fmt(idea.stop_loss)}</b></div>
        <div className="metric"><span>Take profit 1</span><b>{fmt(idea.take_profit_1)}</b></div>
        <div className="metric"><span>Take profit 2</span><b>{fmt(idea.take_profit_2)}</b></div>
      </div>
      <section className="setup-story">
        <div>
          <span><TrendingUp size={14} /> Why this setup?</span>
          <p>{whyThisSetup}</p>
        </div>
        <div>
          <span><ShieldAlert size={14} /> Invalidation</span>
          <p>{idea.invalid_condition}</p>
        </div>
      </section>
      <div className="signal-context-row">
        <span>Regime confidence: {regime.confidence === null ? "Limited" : `${regime.confidence}%`}</span>
        <span>{freshness.detail}</span>
        <span>{liquidity.detail}</span>
        {volatilityWarning ? <span className="warning">Volatility caution</span> : null}
      </div>
      {idea.reversal_confirmations?.length ? (
        <p className="confirmation-list"><b>Confirmations:</b> {idea.reversal_confirmations.join(", ")}</p>
      ) : null}
      {idea.downgraded_reasons?.length ? (
        <p className="confirmation-list"><b>Downgraded:</b> {idea.downgraded_reasons.join(" ")}</p>
      ) : null}
      {(onAiScan || onPaperTrade) ? (
        <div className="trade-card-actions">
          {onAiScan && (
            <button className="secondary-action ai-scan-button" type="button" onClick={() => onAiScan(idea)} disabled={aiLoading}>
              <BrainCircuit size={16} /> {aiLoading ? "Scanning with GenLayer AI..." : "Scan with AI"}
            </button>
          )}
          {onPaperTrade && (
            <button
              className="primary take-trade-button"
              type="button"
              onClick={() => onPaperTrade(idea)}
              disabled={tradeTaken || paperTradeLoading || !canTakeTrade}
            >
              {tradeTaken ? <CheckCircle2 size={16} /> : <Zap size={16} />} {takeTradeLabel}
            </button>
          )}
        </div>
      ) : null}
      {(aiLoading || aiError || aiResult) ? (
        <section className={`ai-consensus decision-${String(aiResult?.decision || "").toLowerCase()}`}>
          <div className="ai-consensus-head">
            <span>GenLayer AI Check</span>
            <b>{aiLoading ? "SCANNING" : aiError ? "ERROR" : aiResult.decision}</b>
          </div>
          {aiLoading ? <p>Scanning with GenLayer AI...</p> : null}
          {aiError ? <p className="ai-warning">GenLayer scan failed. Try again.</p> : null}
          {aiResult && !aiLoading && !aiError ? (
            <>
              <div className="metric-grid ai-metrics">
                <div className="metric"><span>Status</span><b>{aiResult.decision}</b></div>
                <div className="metric"><span>Setup Score</span><b>{aiResult.input?.setup_score}</b></div>
                <div className="metric"><span>RR</span><b>{aiResult.input?.rr}</b></div>
                <div className="metric"><span>Source</span><b>{aiResult.source === "genlayer-studio" ? "Studio Contract" : aiResult.source === "genlayer-endpoint" ? "Endpoint" : "Mock Fallback"}</b></div>
              </div>
              <p><b>Input Used:</b> Setup Score {aiResult.input?.setup_score}, RR {aiResult.input?.rr}</p>
            </>
          ) : null}
        </section>
      ) : null}
    </article>
  );
}
