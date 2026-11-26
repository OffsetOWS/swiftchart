import { ArrowRight } from "lucide-react";
import { formatChangePct } from "../lib/btcMarket.js";
import { formatR, timeAgo } from "../lib/homeSummary.js";
import "../styles/home.css";

export function BtcTicker({ btc }) {
  if (!btc) return null;
  const change = formatChangePct(btc.changePct);
  const tone = btc.changePct > 0.05 ? "up" : btc.changePct < -0.05 ? "down" : "";
  return (
    <span className="tm-ticker" aria-label={`Bitcoin ${btc.priceLabel} dollars, ${change} in 24 hours`}>
      <span className="tm-ticker-sym">BTC</span>
      <span className="tm-ticker-price">{btc.priceLabel}</span>
      {change ? <span className={`tm-ticker-change ${tone}`}>{change}</span> : null}
    </span>
  );
}

export function RegimeCard({ regime, updatedAt, btc }) {
  const updated = timeAgo(updatedAt);
  return (
    <section className="tm-block tm-regime" aria-labelledby="home-regime-title">
      <p className="tm-label">{updated ? `Market · last scan ${updated}` : "Market"}</p>
      <h1 id="home-regime-title" className="tm-statement">{regime.key === "unknown" ? regime.label : `${regime.label}.`}</h1>
      <p className="tm-body">{regime.summary}</p>
      {btc ? (
        <div className="tm-range" aria-label={`BTC ${btc.rangeDays}-day range ${btc.rangeLowLabel} to ${btc.rangeHighLabel}`}>
          <span className="tm-range-line" aria-hidden="true" />
          <span className="tm-range-marker" style={{ left: `${btc.rangePosition}%` }} aria-hidden="true" />
          <span className="tm-range-end low">{btc.rangeLowLabel}</span>
          <span className="tm-range-caption">BTC · {btc.rangeDays}d range</span>
          <span className="tm-range-end high">{btc.rangeHighLabel}</span>
        </div>
      ) : null}
    </section>
  );
}

export function ScanStatusCard({ lastScanAt, refreshing, telegramUrl }) {
  const last = timeAgo(lastScanAt);
  return (
    <section className="tm-block" aria-live="polite">
      <div className="tm-row-head">
        <h2 className="tm-heading">Nothing worth taking yet</h2>
        <span className="tm-meta">{refreshing ? "scanning…" : last ? `scanned ${last}` : ""}</span>
      </div>
      <p className="tm-body tm-quiet">Mid-range trades get skipped on purpose. When something qualifies, it hits Telegram first.</p>
      <a className="tm-link" href={telegramUrl} target="_blank" rel="noreferrer">
        Open the Telegram bot <ArrowRight size={14} aria-hidden="true" />
      </a>
    </section>
  );
}

export function RecentOutcomes({ outcomes, onViewHistory }) {
  if (!outcomes.length) return null;
  return (
    <section className="tm-block" aria-labelledby="home-outcomes-title">
      <div className="tm-row-head">
        <h2 id="home-outcomes-title" className="tm-heading">Closed recently</h2>
        {onViewHistory ? (
          <button type="button" className="tm-link small" onClick={onViewHistory}>History</button>
        ) : null}
      </div>
      <ul className="tm-tape">
        {outcomes.map((item) => {
          const tone = item.r === null ? "" : item.r > 0.05 ? "up" : item.r < -0.05 ? "down" : "";
          return (
            <li key={item.id}>
              <span className="tm-tape-left">
                <b className="tm-mono">{item.symbol}</b>
                <span className={`tm-side ${item.direction}`}>{item.direction}</span>
                <span className="tm-muted">{item.event}</span>
              </span>
              <span className="tm-tape-right">
                <b className={`tm-mono ${tone}`}>{formatR(item.r)}</b>
                <span className="tm-mono tm-muted">{timeAgo(item.closedAt).replace(" ago", "")}</span>
              </span>
            </li>
          );
        })}
      </ul>
      <p className="tm-footnote">R before fees and funding.</p>
    </section>
  );
}
