import { RefreshCcw } from "lucide-react";
import TradeIdeaCard from "../components/TradeIdeaCard.jsx";

export default function Dashboard({ exchange, setExchange, timeframe, setTimeframe, topIdeas, loadingTopIdeas, refreshTopIdeas }) {
  return (
    <div className="dashboard-grid">
      <section className="panel hero-panel">
        <div className="panel-head">
          <div>
            <span className="eyebrow">AI MARKET STRUCTURE ENGINE</span>
            <h1>Trade ideas from live crypto structure.</h1>
            <p>SwiftChart scans support, resistance, liquidity sweeps, range edges, breakouts, and no-trade zones in one premium dashboard.</p>
          </div>
          <button className="icon-btn" onClick={refreshTopIdeas} title="Refresh top ideas"><RefreshCcw size={18} /></button>
        </div>
        <div className="hero-orbit" aria-hidden="true">
          <span />
          <span />
          <span />
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
        <div className="stats bento-stats">
          <div className="stat stat-wide"><span className="muted">Scanner universe</span><b>10 liquid pairs</b><small>BTC, ETH, SOL, OP, ARB and more</small></div>
          <div className="stat"><span className="muted">Risk default</span><b>1%</b></div>
          <div className="stat"><span className="muted">Minimum R:R</span><b>2.0R</b></div>
          <div className="stat"><span className="muted">Execution</span><b>Paper</b></div>
        </div>
        <div className="risk-strip">Trading ideas are not guaranteed profit. Use controlled risk and wait for confirmation.</div>
      </section>
      <aside className="panel ideas-panel">
        <div className="panel-head">
          <h2>Top 5 Trade Ideas</h2>
          <span className="badge">{timeframe}</span>
        </div>
        <div className="idea-list">
          {loadingTopIdeas ? <div className="empty">Scanning markets...</div> : null}
          {!loadingTopIdeas && topIdeas.length === 0 ? <div className="empty">No clean setups found right now.</div> : null}
          {topIdeas.map((idea) => <TradeIdeaCard key={`${idea.symbol}-${idea.direction}-${idea.rank_score}`} idea={idea} />)}
        </div>
      </aside>
    </div>
  );
}
