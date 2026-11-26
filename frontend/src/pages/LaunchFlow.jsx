import { ArrowRight } from "lucide-react";
import { useEffect, useState } from "react";
import { getCandles, getTradeHistory } from "../lib/api.js";
import { useAuth } from "../lib/AuthContext.jsx";
import { formatChangePct, summariseBtcCandles } from "../lib/btcMarket.js";
import { formatR, normalizeOutcomes, timeAgo } from "../lib/homeSummary.js";
import "../styles/welcome.css";

const TELEGRAM_BOT_URL = import.meta.env.VITE_TELEGRAM_BOT_URL || "https://t.me/SwiftChartBot";

export default function LaunchFlow() {
  const auth = useAuth();
  const [btc, setBtc] = useState(null);
  const [outcomes, setOutcomes] = useState([]);

  useEffect(() => {
    let cancelled = false;
    Promise.allSettled([
      getCandles({ exchange: "hyperliquid", symbol: "BTCUSDT", timeframe: "1h" }),
      getTradeHistory({ limit: 40, sort: "desc" }),
    ]).then(([candles, history]) => {
      if (cancelled) return;
      if (candles.status === "fulfilled") setBtc(summariseBtcCandles(candles.value));
      if (history.status === "fulfilled") setOutcomes(normalizeOutcomes(history.value?.records || [], 6));
    });
    return () => {
      cancelled = true;
    };
  }, []);

  const change = btc ? formatChangePct(btc.changePct) : "";
  const changeTone = btc?.changePct > 0.05 ? "up" : btc?.changePct < -0.05 ? "down" : "";

  return (
    <main className="welcome">
      <header className="welcome-nav">
        <a href="/" className="welcome-brand">swiftchart</a>
        {btc ? (
          <span className="welcome-ticker" aria-label={`Bitcoin ${btc.priceLabel}, ${change} in 24 hours`}>
            <span>BTC</span> {btc.priceLabel} <span className={changeTone}>{change}</span>
          </span>
        ) : null}
      </header>

      <section className="welcome-hero">
        <h1>Crypto signals from a scanner that would rather skip a trade than force one.</h1>
        <p>
          SwiftChart reads the market regime, looks for liquidity sweeps at real levels, and only sends a setup when entry, stop and targets all make sense. Every signal comes with the reasoning behind it.
        </p>
        <div className="welcome-actions">
          <button
            className="welcome-primary"
            type="button"
            onClick={() => auth.signInWithGoogle("/app/home").catch(() => {})}
            disabled={!auth.isSupabaseConfigured || auth.loading}
          >
            {auth.loading ? "Checking session…" : "Continue with Google"}
          </button>
          <a className="welcome-secondary" href="/app/home">
            Look around first <ArrowRight size={16} aria-hidden="true" />
          </a>
        </div>
        {auth.error ? <p className="welcome-error" role="alert">{auth.error}</p> : null}
      </section>

      {outcomes.length ? (
        <section className="welcome-tape" aria-labelledby="welcome-tape-title">
          <div className="welcome-tape-head">
            <h2 id="welcome-tape-title">Recently closed</h2>
            <span>R before fees and funding</span>
          </div>
          <ul>
            {outcomes.map((item) => {
              const tone = item.r === null ? "" : item.r > 0.05 ? "up" : item.r < -0.05 ? "down" : "";
              return (
                <li key={item.id}>
                  <span className="welcome-tape-left">
                    <b>{item.symbol}</b>
                    <span className={item.direction === "short" ? "down" : "up"}>{item.direction}</span>
                    <span className="welcome-muted">{item.event}</span>
                  </span>
                  <span className="welcome-tape-right">
                    <b className={tone}>{formatR(item.r)}</b>
                    <span className="welcome-muted">{timeAgo(item.closedAt).replace(" ago", "")}</span>
                  </span>
                </li>
              );
            })}
          </ul>
          <p className="welcome-note">Losses stay on the list. Signals are tracked until they hit a target, the stop, or the 48-hour window closes.</p>
        </section>
      ) : null}

      <section className="welcome-how" aria-label="How SwiftChart works">
        <p><b>The regime comes first.</b> Trending, ranging or breaking down decides which side SwiftChart is allowed to trade.</p>
        <p><b>Then the level.</b> Setups need a sweep and a reclaim at a level that matters, with room to the next target.</p>
        <p><b>Then the plan.</b> You get entry, stop, two targets and the reasoning, on Telegram and in the app.</p>
      </section>

      <footer className="welcome-footer">
        <a href="/docs">Docs</a>
        <a href={TELEGRAM_BOT_URL} target="_blank" rel="noreferrer">Telegram</a>
        <a href="https://x.com/swiftchart_" target="_blank" rel="noreferrer">X</a>
        <span>Analysis, not financial advice.</span>
      </footer>
    </main>
  );
}
