import { Activity, ShieldAlert, Zap } from "lucide-react";

function fmt(value) {
  if (value === undefined || value === null) return "-";
  return Number(value).toLocaleString(undefined, { maximumFractionDigits: 6 });
}

export default function TradeIdeaCard({ idea, onPaperTrade }) {
  const directionClass = idea.direction.toLowerCase();

  return (
    <article className={`idea-card ${directionClass}`}>
      <div className="idea-top">
        <div>
          <h3>{idea.symbol} {idea.timeframe}</h3>
          <p>{idea.reason}</p>
        </div>
        <span className={`badge ${directionClass}`}>
          <Activity size={14} /> {idea.direction}
        </span>
      </div>
      <div className="confidence-rail" aria-hidden="true">
        <span style={{ width: `${Math.min(100, Math.max(0, idea.confidence_score))}%` }} />
      </div>
      <div className="metric-grid">
        <div className="metric"><span>Entry zone</span><b>{fmt(idea.entry_zone[0])} - {fmt(idea.entry_zone[1])}</b></div>
        <div className="metric"><span>Stop loss</span><b>{fmt(idea.stop_loss)}</b></div>
        <div className="metric"><span>Take profit 1</span><b>{fmt(idea.take_profit_1)}</b></div>
        <div className="metric"><span>Take profit 2</span><b>{fmt(idea.take_profit_2)}</b></div>
        <div className="metric"><span>Risk / reward</span><b>{idea.risk_reward_ratio}R</b></div>
        <div className="metric"><span>Confidence</span><b>{idea.confidence_score}%</b></div>
      </div>
      <p style={{ marginTop: 12 }}><ShieldAlert size={14} /> {idea.invalid_condition}</p>
      {onPaperTrade && (
        <button className="primary" style={{ width: "100%", marginTop: 12 }} onClick={() => onPaperTrade(idea)}>
          <Zap size={16} /> Paper trade
        </button>
      )}
    </article>
  );
}
