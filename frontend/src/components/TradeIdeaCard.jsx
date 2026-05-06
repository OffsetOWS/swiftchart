import { Activity, ShieldAlert, Zap } from "lucide-react";

function fmt(value) {
  if (value === undefined || value === null) return "-";
  return Number(value).toLocaleString(undefined, { maximumFractionDigits: 6 });
}

export default function TradeIdeaCard({ idea, onPaperTrade }) {
  const directionClass = idea.direction.toLowerCase();
  const score = idea.setup_score ?? idea.confidence_score;
  const regimeScore = idea.regime_score ?? null;

  return (
    <article className={`idea-card ${directionClass}`}>
      <div className="idea-top">
        <div>
          <h3>{idea.symbol} {idea.timeframe}</h3>
          <span className="exchange-label">{idea.exchange || "exchange"}</span>
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
      <p style={{ marginTop: 12 }}><ShieldAlert size={14} /> {idea.invalid_condition}</p>
      {onPaperTrade && (
        <button className="primary" style={{ width: "100%", marginTop: 12 }} onClick={() => onPaperTrade(idea)}>
          <Zap size={16} /> Paper trade
        </button>
      )}
    </article>
  );
}
