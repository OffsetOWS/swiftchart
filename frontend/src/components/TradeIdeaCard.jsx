import { Activity, BrainCircuit, ShieldAlert, Zap } from "lucide-react";

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
  showTakeTradeButton = false,
}) {
  const directionClass = idea.direction.toLowerCase();
  const score = idea.setup_score ?? idea.confidence_score;
  const regimeScore = idea.regime_score ?? null;

  return (
    <article className={`idea-card ${directionClass}`}>
      <div className="idea-top">
        <div>
          <h3>{idea.symbol} {idea.timeframe}</h3>
          <span className="exchange-label">{sourceLabel(idea.source || idea.exchange)}</span>
          <p>{idea.reason}</p>
        </div>
        <span className={`badge ${directionClass}`}>
          <Activity size={14} /> {idea.direction}
        </span>
      </div>
      <div className="confidence-rail" aria-hidden="true">
        <span style={{ width: `${Math.min(100, Math.max(0, score))}%` }} />
      </div>
      <div className="metric-grid">
        <div className="metric"><span>Setup score</span><b>{score}/100</b></div>
        <div className="metric"><span>Grade</span><b>{idea.setup_grade || "Valid Setup"}</b></div>
        <div className="metric"><span>Market regime</span><b>{idea.regime_label || idea.market_regime || "-"} {regimeScore !== null ? `(${regimeScore > 0 ? "+" : ""}${regimeScore})` : ""}</b></div>
        <div className="metric"><span>Trade bias</span><b>{idea.trend_alignment || "-"}</b></div>
        <div className="metric"><span>HTF bias</span><b>{idea.higher_timeframe_bias || "HTF_NEUTRAL"}</b></div>
        <div className="metric"><span>Regime adjustment</span><b>{idea.regime_confidence_adjustment > 0 ? "+" : ""}{idea.regime_confidence_adjustment || 0}</b></div>
        <div className="metric"><span>Move maturity</span><b>{idea.move_maturity || "Early"}</b></div>
        <div className="metric"><span>Entry status</span><b>{idea.entry_status || "READY"}</b></div>
        <div className="metric"><span>Exhaustion risk</span><b>{idea.exhaustion_risk || "Low"}</b></div>
        <div className="metric"><span>Entry zone</span><b>{fmt(idea.entry_zone[0])} - {fmt(idea.entry_zone[1])}</b></div>
        <div className="metric"><span>Stop loss</span><b>{fmt(idea.stop_loss)}</b></div>
        <div className="metric"><span>Take profit 1</span><b>{fmt(idea.take_profit_1)}</b></div>
        <div className="metric"><span>Take profit 2</span><b>{fmt(idea.take_profit_2)}</b></div>
        <div className="metric"><span>Risk / reward</span><b>{idea.risk_reward_ratio}R</b></div>
        <div className="metric"><span>Confidence</span><b>{idea.confidence_score}%</b></div>
      </div>
      {idea.reversal_confirmations?.length ? (
        <p className="confirmation-list"><b>Confirmations:</b> {idea.reversal_confirmations.join(", ")}</p>
      ) : null}
      {idea.downgraded_reasons?.length ? (
        <p className="confirmation-list"><b>Downgraded:</b> {idea.downgraded_reasons.join(" ")}</p>
      ) : null}
      <p style={{ marginTop: 12 }}><ShieldAlert size={14} /> {idea.invalid_condition}</p>
      {(onAiScan || onPaperTrade || showTakeTradeButton) ? (
        <div className="trade-card-actions">
          {onAiScan && (
            <button className="secondary-action ai-scan-button" type="button" onClick={() => onAiScan(idea)} disabled={aiLoading}>
              <BrainCircuit size={16} /> {aiLoading ? "Scanning with GenLayer AI..." : "Scan with AI"}
            </button>
          )}
          {(onPaperTrade || showTakeTradeButton) && (
            <button
              className="primary take-trade-button"
              type="button"
              onClick={onPaperTrade ? () => onPaperTrade(idea) : undefined}
              disabled={!onPaperTrade || tradeTaken || paperTradeLoading || idea.entry_status !== "READY"}
              title={!onPaperTrade ? "Demo signal only" : undefined}
            >
              <Zap size={16} /> {tradeTaken ? "Trade Taken" : paperTradeLoading ? "Saving..." : idea.entry_status === "WAIT_FOR_RETEST" ? "Wait for Retest" : idea.entry_status === "REJECTED_EXHAUSTED" ? "Rejected" : "Take Trade"}
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
