import { Activity, Bitcoin, Clock3, RefreshCcw, ShieldCheck } from "lucide-react";
import TradeIdeaCard from "../components/TradeIdeaCard.jsx";

const FEATURES = [
  "liquidity sweeps",
  "support & resistance",
  "top 5 trade ideas",
  "Binance data",
  "Hyperliquid data",
  "paper trading",
  "range detection",
  "breakout alerts",
];

export default function Dashboard({ exchange, setExchange, timeframe, setTimeframe, topIdeas, loadingTopIdeas, refreshTopIdeas }) {
  const topIdea = topIdeas[0];

  return (
    <div className="home-flow">
      <section className="portfolio-hero">
        <div className="hero-copy">
          <span className="eyebrow">SWIFTCHART</span>
          <h1>
            <span>SwiftChart</span>
            <span>AI-powered crypto market analysis.</span>
            <span>Find ranges. Detect sweeps. Avoid bad entries.</span>
          </h1>
          <p>SwiftChart helps traders detect support, resistance, liquidity sweeps, range conditions, and high-probability crypto trade ideas.</p>
          <button className="launch-cta" onClick={() => document.getElementById("dashboard-preview")?.scrollIntoView({ behavior: "smooth" })}>
            Launch SwiftChart
          </button>
        </div>
        <div className="hero-chart" aria-hidden="true">
          <span className="chart-line line-a" />
          <span className="chart-line line-b" />
          <span className="chart-line line-c" />
          <span className="chart-node node-a" />
          <span className="chart-node node-b" />
          <span className="chart-node node-c" />
        </div>
      </section>

      <section className="marquee-stack" aria-label="SwiftChart features">
        {FEATURES.map((feature, index) => (
          <div className="marquee-row" key={feature}>
            <div className={`marquee-track ${index % 2 ? "reverse" : ""}`}>
              {Array.from({ length: 8 }).map((_, itemIndex) => (
                <span key={`${feature}-${itemIndex}`}>{feature}</span>
              ))}
            </div>
          </div>
        ))}
      </section>

      <section id="dashboard-preview" className="dashboard-grid dashboard-bento">
        <div className="section-kicker">
          <span className="eyebrow">LIVE PRODUCT PREVIEW</span>
          <h2>Main dashboard preview</h2>
        </div>

        <section className="panel hero-panel bento-hero">
          <div>
            <span className="eyebrow">ANALYSIS CONTROLS</span>
            <h2>Choose your exchange and timeframe.</h2>
            <p>Scan liquid crypto markets for clean range edges, swept liquidity, and breakout conditions.</p>
          </div>
          <button className="icon-btn" onClick={refreshTopIdeas} title="Refresh top ideas"><RefreshCcw size={18} /></button>
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

        <section className="panel strategy-statement">
          <span className="eyebrow">STRATEGY</span>
          <h2>Buy near support, sell near resistance, avoid the middle, and wait for liquidity sweeps.</h2>
          <p>SwiftChart highlights potential setups only. Every idea includes entry, stop, targets, confidence, and invalidation logic.</p>
        </section>

        <section className="panel risk-card">
          Trading ideas are not guaranteed profit. Use controlled risk and wait for confirmation.
        </section>
      </section>
    </div>
  );
}
