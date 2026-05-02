import { Activity, Bitcoin, Clock3, RefreshCcw, ShieldCheck } from "lucide-react";
import TradeIdeaCard from "../components/TradeIdeaCard.jsx";

export default function Dashboard({ exchange, setExchange, timeframe, setTimeframe, topIdeas, loadingTopIdeas, refreshTopIdeas }) {
  const topIdea = topIdeas[0];

  return (
    <div className="dashboard-grid dashboard-bento">
      <section className="panel hero-panel bento-hero">
        <div className="panel-head">
          <div>
            <span className="eyebrow">AI TRADING TERMINAL</span>
            <h1>SwiftChart turns market structure into trade ideas.</h1>
            <p>Scan liquid crypto markets for support, resistance, liquidity sweeps, breakouts, and clean range-edge setups.</p>
          </div>
          <button className="icon-btn" onClick={refreshTopIdeas} title="Refresh top ideas"><RefreshCcw size={18} /></button>
        </div>
        <div className="controls">
          <div className="field">
            <label>Exchange</label>
            <select value={exchange} onChange={(event) => setExchange(event.target.value)}>
              <option value="binance">Binance</option>
              <option value="hyperliquid">Hyperliquid</option>
            </select>
          </div>
          <div className="field">
            <label>Timeframe</label>
            <div className="segmented">
              {["30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d"].map((tf) => (
                <button key={tf} className={timeframe === tf ? "active" : ""} onClick={() => setTimeframe(tf)}>{tf}</button>
              ))}
            </div>
          </div>
        </div>
      </section>

      <section className="panel ideas-panel dominant-ideas">
        <div className="panel-head">
          <div>
            <span className="eyebrow">TOP SETUPS</span>
            <h2>Top 5 Trade Ideas</h2>
          </div>
          <span className="badge">{timeframe}</span>
        </div>
        <div className="idea-list">
          {loadingTopIdeas ? <div className="empty">Scanning markets...</div> : null}
          {!loadingTopIdeas && topIdeas.length === 0 ? <div className="empty">No clean setups found right now.</div> : null}
          {topIdeas.map((idea) => <TradeIdeaCard key={`${idea.symbol}-${idea.direction}-${idea.rank_score}`} idea={idea} />)}
        </div>
      </section>

      <section className="panel mini-card">
        <Activity size={20} />
        <span>Market condition</span>
        <b>{topIdea ? "Setup detected" : "Scanning"}</b>
      </section>
      <section className="panel mini-card">
        <Bitcoin size={20} />
        <span>Selected coin</span>
        <b>{topIdea?.symbol || "SOLUSDT"}</b>
      </section>
      <section className="panel mini-card">
        <Clock3 size={20} />
        <span>Timeframe</span>
        <b>{timeframe}</b>
      </section>
      <section className="panel mini-card">
        <ShieldCheck size={20} />
        <span>Strategy status</span>
        <b>Paper mode</b>
      </section>

      <section className="panel risk-card">
        Trading ideas are not guaranteed profit. Use controlled risk and wait for confirmation.
      </section>
    </div>
  );
}
