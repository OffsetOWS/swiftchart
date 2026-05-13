import { RefreshCcw } from "lucide-react";
import TradeIdeaCard from "../components/TradeIdeaCard.jsx";

export default function Dashboard({ exchange, setExchange, timeframe, setTimeframe, topIdeas, loadingTopIdeas, refreshTopIdeas, onPaperTrade, takenSignalIds = new Set(), paperTradeLoadingSignalId = "", getSignalId, compact = false }) {
  const topIdea = topIdeas[0];
  const regimeLabel = topIdea?.regime_label || "Scanning";
  const regimeScore = topIdea?.regime_score;
  const lastRegimeUpdate = topIdea?.regime_updated_at ? new Date(topIdea.regime_updated_at).toLocaleString() : "-";

  return (
    <div className={compact ? "dashboard-grid compact-dashboard" : "dashboard-grid dashboard-bento"}>
        <section className="panel hero-panel bento-hero" id="about-us">
          <div>
            <span className="eyebrow">MARKET TERMINAL</span>
            <h2>{compact ? "Trade Ideas" : "Dashboard"}</h2>
            <p>Scan liquid markets for clean edges, swept liquidity, and breakout conditions without forcing mid-range noise.</p>
          </div>
          <button className="icon-btn" onClick={refreshTopIdeas} title="Refresh top ideas"><RefreshCcw size={18} /></button>
          <div className="controls">
            <div className="field">
              <label>Exchange</label>
              <select value={exchange} onChange={(event) => setExchange(event.target.value)}>
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

        {!compact ? (
          <section className="panel chart-preview-panel">
            <span className="eyebrow">CHART PREVIEW</span>
            <div className="terminal-chart-preview" aria-hidden="true">
              <span className="range-line support" />
              <span className="range-line resistance" />
              {Array.from({ length: 18 }).map((_, index) => <i key={index} style={{ "--h": `${18 + ((index * 13) % 54)}%` }} />)}
            </div>
            <p>{topIdea ? `${topIdea.symbol} is the current strongest candidate.` : "Waiting for a clean setup from the scanner."}</p>
          </section>
        ) : null}

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
            {topIdeas.map((idea) => {
              const signalId = getSignalId ? getSignalId(idea) : "";
              return (
                <TradeIdeaCard
                  key={`${idea.symbol}-${idea.direction}-${idea.rank_score}`}
                  idea={idea}
                  onPaperTrade={onPaperTrade}
                  tradeTaken={takenSignalIds.has(signalId)}
                  paperTradeLoading={paperTradeLoadingSignalId === signalId}
                />
              );
            })}
          </div>
        </section>

        <section className="panel mini-card regime-card">
          <span>Market regime</span>
          <b>{regimeLabel}</b>
        </section>
        <section className="panel mini-card">
          <span>Regime score</span>
          <b>{regimeScore !== undefined && regimeScore !== null ? `${regimeScore > 0 ? "+" : ""}${regimeScore}` : "-"}</b>
        </section>
        <section className="panel mini-card">
          <span>Long bias</span>
          <b>{topIdea?.regime_label?.includes("Bearish") ? "Reduced" : topIdea ? "Favored" : "-"}</b>
        </section>
        <section className="panel mini-card">
          <span>Short bias</span>
          <b>{topIdea?.regime_label?.includes("Bullish") ? "Reduced" : topIdea ? "Favored" : "-"}</b>
        </section>

        <section className="panel mini-card regime-timestamp">
          <span>Last regime update</span>
          <b>{lastRegimeUpdate}</b>
        </section>

        <section className="panel strategy-statement">
          <span className="eyebrow">STRATEGY</span>
          <h2>Buy near support, sell near resistance, avoid the middle, and wait for liquidity sweeps.</h2>
          <p>SwiftChart highlights potential setups only. Every idea includes entry, stop, targets, confidence, and invalidation logic.</p>
        </section>

        <section className="panel risk-card">
          Trading ideas are not guaranteed profit. Use controlled risk and wait for confirmation.
        </section>
    </div>
  );
}
