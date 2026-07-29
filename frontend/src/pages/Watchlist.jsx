import { RefreshCcw } from "lucide-react";
import InstrumentLogo from "../components/InstrumentLogo.jsx";

function formatZone(zone) {
  if (!Array.isArray(zone) || zone.length < 2) return "-";
  return `${Number(zone[0]).toFixed(4)} - ${Number(zone[1]).toFixed(4)}`;
}

function formatAge(createdAt) {
  if (!createdAt) return "-";
  const created = new Date(createdAt).getTime();
  if (Number.isNaN(created)) return "-";
  const minutes = Math.max(0, Math.floor((Date.now() - created) / 60000));
  if (minutes < 60) return `${minutes}m`;
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}

function directionClass(direction) {
  return direction === "Long" ? "long" : "short";
}

export default function Watchlist({
  pendingSetups = [],
  loading = false,
  exchange,
  setExchange,
  timeframe,
  setTimeframe,
  onRefresh,
  meta = {},
}) {
  return (
    <section className="watchlist-page">
      <div className="panel watchlist-hero">
        <div>
          <span className="eyebrow">WEBSITE WATCHLIST</span>
          <h2>Pending Setups</h2>
          <p>Early structure worth watching. These are not trade signals yet and cannot be taken, sent to Telegram, or passed to execution.</p>
        </div>
        <button className="icon-btn" onClick={() => onRefresh({ manual: true })} title="Refresh watchlist">
          <RefreshCcw size={18} />
        </button>
        <div className="controls">
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
            <div className="segmented">
              {["30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d"].map((tf) => (
                <button key={tf} className={timeframe === tf ? "active" : ""} onClick={() => setTimeframe(tf)}>{tf}</button>
              ))}
            </div>
          </div>
        </div>
      </div>

      <div className="watchlist-grid">
        {meta.refreshing ? <div className="panel empty watchlist-refreshing">Refreshing cached watchlist...</div> : null}
        {loading ? <div className="panel empty">Loading cached watchlist...</div> : null}
        {!loading && pendingSetups.length === 0 ? <div className="panel empty">No pending setups worth watching right now.</div> : null}
        {pendingSetups.map((setup) => (
          <article className="panel watchlist-card" key={`${setup.symbol}-${setup.direction}-${setup.status}-${setup.created_at}`}>
            <div className="watchlist-card-head">
              <div className="watchlist-asset-title">
                <InstrumentLogo symbol={setup.symbol} marketType={setup.marketType || setup.market} size={42} />
                <div>
                  <span className="watchlist-label">Not a trade signal yet</span>
                  <h3>{setup.symbol}</h3>
                </div>
              </div>
              <div className={`direction-pill ${directionClass(setup.direction)}`}>{setup.direction}</div>
            </div>

            <div className="watchlist-meta">
              <span>{setup.regime}</span>
              <b>{setup.status.replaceAll("_", " ")}</b>
            </div>

            <p className="watchlist-reason">{setup.reason}</p>

            <div className="watchlist-columns">
              <div>
                <span>Entry zone</span>
                <b>{formatZone(setup.entry_zone)}</b>
              </div>
              <div>
                <span>Invalidation</span>
                <b>{Number(setup.invalidation_level).toFixed(4)}</b>
              </div>
              <div>
                <span>Estimated R:R</span>
                <b>{setup.estimated_rr ? setup.estimated_rr.toFixed(2) : "-"}</b>
              </div>
              <div>
                <span>Age</span>
                <b>{formatAge(setup.created_at)}</b>
              </div>
            </div>

            <div className="watchlist-detail">
              <div>
                <span>Trigger hints</span>
                <p>{setup.trigger_hints?.length ? setup.trigger_hints.join(", ") : "-"}</p>
              </div>
              <div>
                <span>Confirmation needed</span>
                <p>{setup.confirmation_needed?.length ? setup.confirmation_needed.join(", ") : "-"}</p>
              </div>
            </div>

            <div className="watchlist-range">
              <span>Support {formatZone(setup.nearest_support)}</span>
              <span>Position {setup.price_position !== null && setup.price_position !== undefined ? `${Math.round(setup.price_position * 100)}%` : "-"}</span>
              <span>Resistance {formatZone(setup.nearest_resistance)}</span>
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}
