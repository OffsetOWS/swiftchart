import { ArrowDownRight, ArrowUpRight, BarChart3, Flame, ShieldCheck } from "lucide-react";
import { formatChange, formatCompactUsd } from "../lib/marketIntelligence.js";
import "../styles/marketIntelligence.css";

const sections = [
  { key: "trending", label: "Trending Assets", icon: Flame },
  { key: "top_gainers", label: "Top Gainers", icon: ArrowUpRight },
  { key: "top_losers", label: "Top Losers", icon: ArrowDownRight },
  { key: "highest_quality", label: "Highest Market Quality", icon: ShieldCheck },
];

function AssetTable({ assets, showChange, onAnalyzeAsset }) {
  if (!assets?.length) return <div className="market-intelligence-empty">CMC data is temporarily unavailable.</div>;
  return (
    <div className="market-asset-table">
      <div className="market-asset-row market-asset-head">
        <span>Asset</span><span>Market Cap</span><span>24h Volume</span><span>Rank</span><span>Action</span>
      </div>
      {assets.slice(0, 5).map((asset) => (
        <button
          className="market-asset-row market-asset-action"
          key={`${asset.symbol}-${asset.cmc_rank}`}
          type="button"
          onClick={() => onAnalyzeAsset?.(asset)}
          aria-label={`Analyze ${asset.name || asset.symbol}`}
        >
          <b>{asset.symbol}<small>{showChange ? formatChange(asset.price_change_24h) : asset.market_quality_score ? `${asset.market_quality_score}/100` : ""}</small></b>
          <span>{formatCompactUsd(asset.market_cap)}</span>
          <span>{formatCompactUsd(asset.volume_24h)}</span>
          <span>{asset.cmc_rank ? `#${asset.cmc_rank}` : "-"}</span>
          <strong>Analyze →</strong>
        </button>
      ))}
    </div>
  );
}

export default function MarketIntelligenceWidget({ intelligence, loading = false, onAnalyzeAsset }) {
  return (
    <section className="panel market-intelligence-widget">
      <div className="panel-head">
        <div>
          <span className="eyebrow">MARKET INTELLIGENCE</span>
          <h2>Market Intelligence</h2>
          <p>Discover trending assets, analyze market quality, and uncover trading opportunities.</p>
        </div>
        <span className="cmc-powered-badge"><BarChart3 size={14} /> Powered by CoinMarketCap Market Data</span>
      </div>
      <div className="market-intelligence-sections">
        {sections.map(({ key, label, icon: Icon }) => (
          <section className="market-intelligence-group" key={key}>
            <h3><Icon size={15} /> {label}</h3>
            {loading ? <div className="market-intelligence-empty">Loading market context...</div> : (
              <AssetTable
                assets={intelligence?.[key]}
                showChange={key === "top_gainers" || key === "top_losers"}
                onAnalyzeAsset={onAnalyzeAsset}
              />
            )}
          </section>
        ))}
      </div>
    </section>
  );
}
