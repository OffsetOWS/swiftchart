import { BarChart3, Search, ShieldCheck, Target, Waves } from "lucide-react";
import Chart from "../components/Chart.jsx";
import TradeIdeaCard from "../components/TradeIdeaCard.jsx";
import { formatCompactUsd, formatUsdPrice, marketDataForSignal } from "../lib/marketIntelligence.js";
import useMarketIntelligence from "../lib/useMarketIntelligence.js";
import "../styles/marketIntelligence.css";

function formatLevel(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "-";
  return number.toLocaleString(undefined, { maximumFractionDigits: 6 });
}

function zoneRange(zone) {
  if (!zone) return "-";
  return `${formatLevel(zone.lower)} – ${formatLevel(zone.upper)}`;
}

function regimeLabel(analysis) {
  const value = String(analysis?.market_condition || analysis?.market_regime_data?.regime_type || "").toUpperCase();
  if (value.includes("VOLAT")) return "High Volatility";
  if (value.includes("BULL")) return "Bullish";
  if (value.includes("BEAR")) return "Bearish";
  if (value.includes("RANGE") || value.includes("CHOP")) return "Range";
  return analysis ? "Range" : "Awaiting Analysis";
}

function levelContext(analysis) {
  if (!analysis) return {};
  const price = Number(analysis.current_price);
  const supports = analysis.support_zones || [];
  const resistances = analysis.resistance_zones || [];
  const nearestSupport = supports
    .filter((zone) => Number(zone.upper) <= price)
    .sort((a, b) => Number(b.upper) - Number(a.upper))[0] || supports[0];
  const nearestResistance = resistances
    .filter((zone) => Number(zone.lower) >= price)
    .sort((a, b) => Number(a.lower) - Number(b.lower))[0] || resistances[0];
  const majorSupport = [...supports].sort((a, b) => Number(b.strength_score || b.strength) - Number(a.strength_score || a.strength))[0];
  const majorResistance = [...resistances].sort((a, b) => Number(b.strength_score || b.strength) - Number(a.strength_score || a.strength))[0];
  return { nearestSupport, nearestResistance, majorSupport, majorResistance };
}

export default function Analysis({
  state,
  setters,
  candles,
  analysis,
  loading,
  onAnalyze,
  onPaperTrade,
  takenSignalIds = new Set(),
  paperTradeLoadingSignalId = "",
  getSignalId,
  onAiScan,
  aiResults = {},
  aiErrors = {},
  aiLoadingSignalId = "",
  analysisError = "",
}) {
  const { symbol, exchange, timeframe, risk } = state;
  const { setSymbol, setExchange, setTimeframe, setRisk } = setters;
  const marketSymbols = [symbol, ...(analysis?.trade_ideas || []).map((idea) => idea.symbol)];
  const { data: marketIntelligence, loading: marketIntelligenceLoading } = useMarketIntelligence(marketSymbols);
  const marketData = marketDataForSignal(marketIntelligence, symbol);
  const levels = levelContext(analysis);
  const currentPrice = analysis?.current_price ?? marketData?.price;
  const hasOpportunity = Boolean(analysis?.trade_ideas?.length);
  const marketOnly = Boolean(marketData && !analysis && analysisError);

  return (
    <div className="analysis-grid">
      <section className="panel analysis-intelligence-hero">
        <div className="analysis-intelligence-heading">
          <div>
            <span className="eyebrow">MARKET INTELLIGENCE</span>
            <h1>{marketData?.name || symbol.replace(/USDT$/i, "")}</h1>
            <p>CoinMarketCap discovery enriched with SwiftChart market structure and opportunity analysis.</p>
          </div>
          <span className="cmc-powered-badge"><BarChart3 size={14} /> Powered by CoinMarketCap Market Data</span>
        </div>
        <div className="asset-overview-grid">
          <div><span>Symbol</span><b>{marketData?.symbol || symbol.replace(/USDT$/i, "")}</b></div>
          <div><span>Current Price</span><b>{formatUsdPrice(currentPrice)}</b></div>
          <div><span>Market Cap</span><b>{formatCompactUsd(marketData?.market_cap)}</b></div>
          <div><span>24h Volume</span><b>{formatCompactUsd(marketData?.volume_24h)}</b></div>
          <div><span>CMC Rank</span><b>{marketData?.cmc_rank ? `#${marketData.cmc_rank}` : "-"}</b></div>
          <div><span>Market Quality</span><b>{marketData ? `${marketData.market_quality_score}/100` : "-"}</b></div>
        </div>
        {marketOnly ? <div className="market-coming-soon">Market intelligence available. SwiftChart analysis coming soon.</div> : null}
      </section>

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
            <option value="all">All</option>
            <option value="hyperliquid">Hyperliquid</option>
            <option value="variational">Variational</option>
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
        <span className="eyebrow">ACTIONABLE INTELLIGENCE</span>
        <h2>Analysis Summary</h2>
        {analysis ? (
          <>
            <div className="analysis-summary-block">
              <h3><Waves size={15} /> Current Market Regime</h3>
              <b className="analysis-regime-value">{regimeLabel(analysis)}</b>
            </div>
            <div className="analysis-summary-block">
              <h3><ShieldCheck size={15} /> Support &amp; Resistance</h3>
              <div className="analysis-level-grid">
                <div><span>Nearest Support</span><b>{zoneRange(levels.nearestSupport)}</b></div>
                <div><span>Nearest Resistance</span><b>{zoneRange(levels.nearestResistance)}</b></div>
                <div><span>Major Support</span><b>{zoneRange(levels.majorSupport)}</b></div>
                <div><span>Major Resistance</span><b>{zoneRange(levels.majorResistance)}</b></div>
              </div>
            </div>
            <div className="analysis-summary-block trade-opportunity-block">
              <h3><Target size={15} /> Trade Opportunity</h3>
              {!hasOpportunity ? <div className="market-coming-soon">No high-quality trade opportunity currently available.</div> : null}
              <div className="idea-list">
              {analysis.trade_ideas.map((idea) => {
                const signalId = getSignalId ? getSignalId(idea) : "";
                return (
                  <TradeIdeaCard
                    key={`${idea.direction}-${idea.entry_zone[0]}`}
                    idea={idea}
                    onPaperTrade={onPaperTrade}
                    tradeTaken={takenSignalIds.has(signalId)}
                    paperTradeLoading={paperTradeLoadingSignalId === signalId}
                    onAiScan={onAiScan}
                    aiResult={aiResults[signalId]}
                    aiError={aiErrors[signalId]}
                    aiLoading={aiLoadingSignalId === signalId}
                    marketData={marketDataForSignal(marketIntelligence, idea.symbol)}
                    marketDataLoading={marketIntelligenceLoading}
                  />
                );
              })}
              </div>
            </div>
          </>
        ) : (
          <div className="empty">
            {marketOnly ? "Market intelligence available. SwiftChart analysis coming soon." : loading ? "Running SwiftChart analysis..." : "No analysis loaded yet."}
          </div>
        )}
      </aside>
    </div>
  );
}
