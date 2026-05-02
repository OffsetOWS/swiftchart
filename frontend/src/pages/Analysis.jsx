import { Search } from "lucide-react";
import Chart from "../components/Chart.jsx";
import TradeIdeaCard from "../components/TradeIdeaCard.jsx";

export default function Analysis({ state, setters, candles, analysis, loading, onAnalyze, onPaperTrade }) {
  const { symbol, exchange, timeframe, risk } = state;
  const { setSymbol, setExchange, setTimeframe, setRisk } = setters;

  return (
    <div className="analysis-grid">
      <aside className="panel control-panel">
        <span className="eyebrow">LIVE ANALYSIS</span>
        <h2>Coin Analysis</h2>
        <div className="field" style={{ marginTop: 14 }}>
          <label>Symbol</label>
          <input value={symbol} onChange={(event) => setSymbol(event.target.value.toUpperCase())} placeholder="SOLUSDT" />
        </div>
        <div className="field">
          <label>Exchange</label>
          <select value={exchange} onChange={(event) => setExchange(event.target.value)}>
            <option value="binance">Binance</option>
            <option value="hyperliquid">Hyperliquid</option>
          </select>
        </div>
        <div className="field">
          <label>Timeframe</label>
          <select value={timeframe} onChange={(event) => setTimeframe(event.target.value)}>
            {["30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d"].map((tf) => <option key={tf} value={tf}>{tf}</option>)}
          </select>
        </div>
        <div className="field">
          <label>Account size</label>
          <input type="number" value={risk.accountSize} onChange={(event) => setRisk({ ...risk, accountSize: event.target.value })} />
        </div>
        <div className="field">
          <label>Risk per trade %</label>
          <input type="number" step="0.1" value={risk.riskPerTrade} onChange={(event) => setRisk({ ...risk, riskPerTrade: event.target.value })} />
        </div>
        <div className="field">
          <label>Minimum R:R</label>
          <input type="number" step="0.1" value={risk.minRR} onChange={(event) => setRisk({ ...risk, minRR: event.target.value })} />
        </div>
        <div className="field">
          <label>Max open trades</label>
          <input type="number" value={risk.maxOpenTrades} onChange={(event) => setRisk({ ...risk, maxOpenTrades: event.target.value })} />
        </div>
        <button className="primary" style={{ width: "100%" }} onClick={onAnalyze}>
          <Search size={16} /> Analyze
        </button>
      </aside>

      <section className="chart-panel">
        <div className="panel-head">
          <div>
            <span className="eyebrow">MARKET MAP</span>
            <h2>{symbol} Structure</h2>
            <p>{analysis ? `${analysis.market_condition} at ${analysis.current_price}` : "Fetch candles to run the strategy."}</p>
          </div>
          <span className="badge">{loading ? "Loading" : exchange}</span>
        </div>
        <Chart candles={candles} analysis={analysis} />
        {analysis?.warning ? <div className="risk-strip">{analysis.warning}</div> : null}
      </section>

      <aside className="panel verdict-panel">
        <span className="eyebrow">SIGNAL STACK</span>
        <h2>Strategy Verdict</h2>
        {analysis ? (
          <>
            <div className="stats" style={{ gridTemplateColumns: "1fr 1fr" }}>
              <div className="stat"><span className="muted">Condition</span><b>{analysis.market_condition}</b></div>
              <div className="stat"><span className="muted">Sweeps</span><b>{analysis.liquidity_sweeps.length}</b></div>
            </div>
            <div className="zone-list">
              {analysis.support_zones.slice(0, 2).map((zone) => <div className="zone" key={`s-${zone.lower}`}><span>Support</span><b>{zone.lower.toFixed(4)} - {zone.upper.toFixed(4)}</b></div>)}
              {analysis.resistance_zones.slice(0, 2).map((zone) => <div className="zone" key={`r-${zone.lower}`}><span>Resistance</span><b>{zone.lower.toFixed(4)} - {zone.upper.toFixed(4)}</b></div>)}
            </div>
            <div className="idea-list" style={{ marginTop: 14 }}>
              {analysis.trade_ideas.length === 0 ? <div className="empty">No valid setup. Avoid forcing mid-range entries.</div> : null}
              {analysis.trade_ideas.map((idea) => <TradeIdeaCard key={`${idea.direction}-${idea.entry_zone[0]}`} idea={idea} onPaperTrade={onPaperTrade} />)}
            </div>
          </>
        ) : <div className="empty">No analysis loaded yet.</div>}
      </aside>
    </div>
  );
}
