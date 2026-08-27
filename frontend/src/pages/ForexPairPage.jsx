import { useCallback, useEffect, useMemo, useState } from "react";
import { ArrowLeft, RefreshCw, Star } from "lucide-react";
import ForexPairChart from "../components/ForexPairChart.jsx";
import GraphiteStateCard from "../components/GraphiteStateCard.jsx";
import { useAuth } from "../lib/AuthContext.jsx";
import { getForexPairChart } from "../lib/api.js";
import { loadAppPreferences } from "../lib/appPreferences.js";
import { forexZoneStatus } from "../lib/forexPairState.js";
import "../styles/forexPair.css";

const TIMEFRAMES = ["1H", "4H", "1D"];

function price(value, symbol) {
  if (!Number.isFinite(Number(value))) return "—";
  const digits = symbol === "XAUUSD" || Number(value) >= 100 ? 2 : 5;
  return Number(value).toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

function words(value) {
  return String(value || "").replaceAll("_", " ");
}

export default function ForexPairPage({ symbol, onNavigate }) {
  const auth = useAuth();
  const preferences = loadAppPreferences(auth.user?.id);
  const light = preferences.appearance.theme === "light";
  const [timeframe, setTimeframe] = useState("1H");
  const [snapshot, setSnapshot] = useState(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async ({ quiet = false } = {}) => {
    if (quiet) setRefreshing(true); else setLoading(true);
    setError("");
    try {
      setSnapshot(await getForexPairChart(symbol, timeframe));
    } catch (requestError) {
      setError(requestError.message || "Forex chart data is temporarily unavailable.");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [symbol, timeframe]);

  useEffect(() => {
    load();
    const timer = window.setInterval(() => load({ quiet: true }), 60_000);
    return () => window.clearInterval(timer);
  }, [load]);

  const primaryZone = useMemo(
    () => snapshot?.bias === "BEARISH" ? snapshot?.sell_zones?.[0] : snapshot?.buy_zones?.[0],
    [snapshot],
  );
  const change = Number(snapshot?.change_percentage || 0);
  const decisionTone = snapshot?.decision === "LONG" ? "long" : snapshot?.decision === "SHORT" ? "short" : "wait";

  return (
    <section className={`graphite-app forex-pair-page ${light ? "is-light" : ""}`}>
      <header className="forex-pair-topbar">
        <button type="button" onClick={() => onNavigate("/app/home?market=forex")} aria-label="Back to Forex">
          <ArrowLeft size={20} /><span>Forex</span>
        </button>
        <button type="button" className="forex-pair-favorite" aria-label={`Favorite ${symbol}`}><Star size={20} /></button>
      </header>

      <main className="forex-pair-scroll">
        <section className="forex-pair-heading">
          <div><h1>{symbol}</h1><p>{snapshot?.display_name || "Forex pair"}</p></div>
        </section>

        {loading && !snapshot ? (
          <div className="forex-pair-loading" aria-live="polite"><span /><p>Loading OANDA candles and Swift TA…</p></div>
        ) : null}
        {error && !snapshot ? (
          <GraphiteStateCard tone="error" eyebrow="Market data" title="Chart unavailable" message={error} actionLabel="Try again" onAction={() => load()} />
        ) : null}

        {snapshot ? <>
          <section className="forex-pair-quote">
            <strong>{price(snapshot.current_price, symbol)}</strong>
            <span className={change >= 0 ? "positive" : "negative"}>{change >= 0 ? "+" : ""}{change.toFixed(2)}%</span>
            <button type="button" onClick={() => load({ quiet: true })} disabled={refreshing} aria-label="Refresh chart"><RefreshCw size={16} /></button>
          </section>

          <div className="forex-timeframe-tabs" aria-label="Chart timeframe">
            {TIMEFRAMES.map((value) => <button key={value} type="button" className={timeframe === value ? "active" : ""} onClick={() => setTimeframe(value)}>{value}</button>)}
          </div>

          <section className="forex-bias-row">
            <div><span>{words(snapshot.regime)}</span><small>{snapshot.bias} bias</small></div>
            <strong className={decisionTone}>{words(snapshot.decision)}</strong>
          </section>

          <ForexPairChart snapshot={snapshot} light={light} />

          <section className="swift-ta-card">
            <header><div><span>Swift TA</span><small>Updated {new Date(snapshot.updated_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</small></div><strong className={decisionTone}>{forexZoneStatus(snapshot)}</strong></header>
            <h2>{words(snapshot.decision)}</h2>
            <p>{snapshot.summary}</p>
            <dl>
              {primaryZone ? <div><dt>{snapshot.bias === "BEARISH" ? "Sell zone" : "Buy zone"}<small>{words(primaryZone.state)}</small></dt><dd>{price(primaryZone.low, symbol)}–{price(primaryZone.high, symbol)}</dd></div> : null}
              <div><dt>Confirmation</dt><dd>{snapshot.bias === "BEARISH" ? "<" : ">"}{price(snapshot.levels.confirmation, symbol)}</dd></div>
              <div><dt>Target 1</dt><dd>{price(snapshot.levels.target_1, symbol)}</dd></div>
              <div><dt>Target 2</dt><dd>{price(snapshot.levels.target_2, symbol)}</dd></div>
              <div className="invalidation"><dt>Invalidation</dt><dd>{snapshot.bias === "BEARISH" ? ">" : "<"}{price(snapshot.levels.invalidation, symbol)}</dd></div>
            </dl>
          </section>
          {error ? <p className="forex-stale-note">Live refresh failed. Showing the latest available snapshot.</p> : null}
        </> : null}
      </main>
    </section>
  );
}
