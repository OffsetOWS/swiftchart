import { useCallback, useEffect, useState } from "react";
import { ArrowLeft, RefreshCw } from "lucide-react";
import { CurrencyStrengthOpportunities, CurrencyStrengthRanking } from "../components/CurrencyStrength.jsx";
import GraphiteStateCard from "../components/GraphiteStateCard.jsx";
import { useAuth } from "../lib/AuthContext.jsx";
import { getForexCurrencyStrength } from "../lib/api.js";
import { loadAppPreferences } from "../lib/appPreferences.js";
import "../styles/forexPair.css";

const HORIZONS = ["1H", "4H", "24H"];

export default function CurrencyStrengthPage({ onNavigate }) {
  const auth = useAuth();
  const preferences = loadAppPreferences(auth.user?.id);
  const light = preferences.appearance.theme === "light";
  const [horizon, setHorizon] = useState("24H");
  const [snapshot, setSnapshot] = useState(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async ({ quiet = false } = {}) => {
    if (quiet) setRefreshing(true); else setLoading(true);
    setError("");
    try {
      setSnapshot(await getForexCurrencyStrength(horizon));
    } catch (requestError) {
      setError(requestError.message || "Currency strength is temporarily unavailable.");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [horizon]);

  useEffect(() => {
    load();
    const refreshTimer = window.setInterval(() => load({ quiet: true }), 60_000);
    return () => window.clearInterval(refreshTimer);
  }, [load]);

  return (
    <section className={`graphite-app forex-pair-page currency-strength-page ${light ? "is-light" : ""}`}>
      <header className="forex-pair-topbar">
        <button type="button" onClick={() => onNavigate("/app/home?market=forex")} aria-label="Back to Forex">
          <ArrowLeft size={20} /><span>Forex</span>
        </button>
        <button type="button" className="forex-pair-favorite" onClick={() => load({ quiet: true })} disabled={refreshing} aria-label="Refresh currency strength">
          <RefreshCw size={19} />
        </button>
      </header>

      <main className="forex-pair-scroll">
        <section className="currency-strength-heading">
          <span>OANDA · LIVE · informational</span>
          <h1>Currency Strength</h1>
          <p>Relative price-change strength across all 28 major currency crosses.</p>
        </section>

        <div className="forex-timeframe-tabs" aria-label="Currency strength horizon">
          {HORIZONS.map((value) => (
            <button key={value} type="button" className={horizon === value ? "active" : ""} onClick={() => setHorizon(value)}>{value}</button>
          ))}
        </div>

        {loading && !snapshot ? <div className="forex-pair-loading"><span /><p>Calculating OANDA currency strength…</p></div> : null}
        {error && !snapshot ? <GraphiteStateCard tone="error" eyebrow="OANDA data" title="Strength unavailable" message={error} actionLabel="Try again" onAction={() => load()} /> : null}

        {snapshot ? <>
          <section className="currency-strength-panel">
            <header><div><span>{snapshot.horizon} ranking</span><small>{snapshot.available_pair_count}/{snapshot.total_pair_count} crosses available</small></div><time>{new Date(snapshot.updated_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</time></header>
            <CurrencyStrengthRanking rows={snapshot.ranking} />
          </section>

          <section className="currency-strength-panel">
            <header><div><span>Top pair opportunities</span><small>Largest strongest-vs-weakest gaps</small></div></header>
            <CurrencyStrengthOpportunities opportunities={snapshot.opportunities} onSelectPair={(pair) => onNavigate(`/app/forex/${pair}`)} />
          </section>
          {error ? <p className="forex-stale-note">Refresh failed. Showing the latest strength snapshot.</p> : null}
        </> : null}
      </main>
    </section>
  );
}
