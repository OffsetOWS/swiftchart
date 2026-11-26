import { ArrowUpRight, Send } from "lucide-react";
import { formatR, timeAgo } from "../lib/homeSummary.js";
import "../styles/home.css";

export function RegimeCard({ regime, updatedAt }) {
  const updated = timeAgo(updatedAt);
  return (
    <section className="home-card home-regime" aria-labelledby="home-regime-title">
      <div className="home-card-head">
        <span className="home-eyebrow">Market regime</span>
        {updated ? <span className="home-meta">Updated {updated}</span> : null}
      </div>
      <h1 id="home-regime-title" className="home-regime-title">{regime.label}</h1>
      <p className="home-body">{regime.summary}</p>
      {regime.position !== null ? (
        <div className="home-regime-scale" aria-hidden="true">
          <span className="home-regime-track" />
          <span className="home-regime-marker" style={{ left: `${regime.position}%` }} />
          <div className="home-regime-labels"><span>Down</span><span>Range</span><span>Up</span></div>
        </div>
      ) : null}
    </section>
  );
}

export function ScanStatusCard({ lastScanAt, refreshing, telegramUrl }) {
  const last = timeAgo(lastScanAt);
  return (
    <section className="home-card" aria-live="polite">
      <div className="home-card-head">
        <span className="home-eyebrow">Market scan</span>
        <span className="home-meta">{refreshing ? "Scanning now…" : last ? `Last scan ${last}` : ""}</span>
      </div>
      <h2 className="home-title">No setup passes the filter yet</h2>
      <p className="home-body">
        Weak mid-range trades are skipped on purpose. You'll get the signal on Telegram the moment one qualifies.
      </p>
      <a className="home-primary" href={telegramUrl} target="_blank" rel="noreferrer">
        <Send size={15} aria-hidden="true" />
        Get alerts on Telegram
      </a>
    </section>
  );
}

export function RecentOutcomes({ outcomes, onViewHistory }) {
  if (!outcomes.length) return null;
  return (
    <section className="home-card home-outcomes" aria-labelledby="home-outcomes-title">
      <div className="home-card-head">
        <span id="home-outcomes-title" className="home-eyebrow">Recent outcomes</span>
        {onViewHistory ? (
          <button type="button" className="home-link" onClick={onViewHistory}>
            History <ArrowUpRight size={13} aria-hidden="true" />
          </button>
        ) : null}
      </div>
      <ul className="home-outcome-list">
        {outcomes.map((item) => {
          const tone = item.r === null ? "" : item.r > 0.05 ? "is-win" : item.r < -0.05 ? "is-loss" : "";
          return (
            <li key={item.id} className="home-outcome">
              <div>
                <strong>{item.symbol}</strong>
                <span className={`home-side ${item.direction}`}>{item.direction === "short" ? "Short" : "Long"}</span>
                <span className="home-outcome-event">{item.event}</span>
              </div>
              <div className="home-outcome-right">
                <b className={tone}>{formatR(item.r)}</b>
                <span>{timeAgo(item.closedAt)}</span>
              </div>
            </li>
          );
        })}
      </ul>
      <p className="home-footnote">Results in R before fees and funding.</p>
    </section>
  );
}
