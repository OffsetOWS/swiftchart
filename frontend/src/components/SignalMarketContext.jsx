import { BarChart3, DatabaseZap } from "lucide-react";
import { formatChange, formatCompactUsd } from "../lib/marketIntelligence.js";

export default function SignalMarketContext({ marketData, loading = false }) {
  if (loading) {
    return (
      <section className="signal-market-context is-loading">
        <div className="signal-market-context-head">
          <span><BarChart3 size={14} /> Market Intelligence</span>
          <b>LOADING CMC CONTEXT</b>
        </div>
      </section>
    );
  }

  if (!marketData) {
    return (
      <section className="signal-market-context is-unavailable">
        <div className="signal-market-context-head">
          <span><BarChart3 size={14} /> Market Intelligence</span>
          <b>CONTEXT UNAVAILABLE</b>
        </div>
        <p>SwiftChart signal generation is unaffected and continues with Hyperliquid market data.</p>
      </section>
    );
  }

  const qualityTone = marketData.market_quality_score >= 75
    ? "high"
    : marketData.market_quality_score >= 55
      ? "medium"
      : "risk";

  return (
    <section className={`signal-market-context quality-${qualityTone}`}>
      <div className="signal-market-context-head">
        <span><BarChart3 size={14} /> Market Intelligence</span>
        <b><DatabaseZap size={13} /> COINMARKETCAP</b>
      </div>
      <div className="signal-market-grid">
        <div><span>Market Cap</span><b>{formatCompactUsd(marketData.market_cap)}</b></div>
        <div><span>24h Volume</span><b>{formatCompactUsd(marketData.volume_24h)}</b></div>
        <div><span>CMC Rank</span><b>{marketData.cmc_rank ? `#${marketData.cmc_rank}` : "-"}</b></div>
        <div><span>24h Change</span><b>{formatChange(marketData.price_change_24h)}</b></div>
      </div>
      <div className="market-quality-summary">
        <div>
          <span>Market Quality Score</span>
          <b>{marketData.market_quality_score}<small>/100</small></b>
        </div>
        <div className="market-quality-rail" aria-hidden="true">
          <span style={{ width: `${marketData.market_quality_score}%` }} />
        </div>
        <strong>{marketData.quality_label}</strong>
      </div>
      <p>Informational context only — this score does not affect the SwiftChart trade signal.</p>
    </section>
  );
}
