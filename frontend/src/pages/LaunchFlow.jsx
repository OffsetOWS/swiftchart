import { ArrowRight, Chrome, Layers, Send, ShieldCheck } from "lucide-react";
import { useAuth } from "../lib/AuthContext.jsx";
import "../styles/welcome.css";

const TELEGRAM_BOT_URL = import.meta.env.VITE_TELEGRAM_BOT_URL || "https://t.me/SwiftChartBot";

const features = [
  [Layers, "Reads the regime first", "Every scan starts by classifying the market, so signals fit the conditions instead of fighting them."],
  [ShieldCheck, "Only filtered setups", "Mid-range noise and exhausted moves are rejected. What reaches you has a defined entry, stop and targets."],
  [Send, "Delivered to Telegram", "Signals arrive with the context behind the trade, written like an analyst's note."],
];

export default function LaunchFlow() {
  const auth = useAuth();
  const canSignIn = auth.isSupabaseConfigured && !auth.loading;

  return (
    <main className="welcome">
      <header className="welcome-nav">
        <a href="/app/home" className="welcome-brand" aria-label="SwiftChart home">
          <span>SWIFTCHART</span>
        </a>
        <nav aria-label="SwiftChart links">
          <a href="/docs">Docs</a>
          <a href="https://x.com/swiftchart_" target="_blank" rel="noreferrer">X</a>
          <a href={TELEGRAM_BOT_URL} target="_blank" rel="noreferrer">Telegram</a>
        </nav>
      </header>

      <section className="welcome-hero">
        <span className="welcome-eyebrow">Crypto market intelligence</span>
        <h1>Trade the market you're in, not the one you hope for.</h1>
        <p>
          SwiftChart scans liquid perpetual markets, reads the regime, and only sends setups that pass its risk and quality filters.
        </p>
        <div className="welcome-actions">
          <button
            className="welcome-primary"
            type="button"
            onClick={() => auth.signInWithGoogle("/app/home").catch(() => {})}
            disabled={!canSignIn}
          >
            <Chrome size={17} aria-hidden="true" />
            {auth.loading ? "Checking session…" : "Continue with Google"}
          </button>
          <a className="welcome-secondary" href="/app/home">
            Open the app <ArrowRight size={16} aria-hidden="true" />
          </a>
        </div>
        {!auth.isSupabaseConfigured ? <p className="welcome-note">Sign-in isn't configured on this deployment.</p> : null}
        {auth.error ? <p className="welcome-error" role="alert">{auth.error}</p> : null}
      </section>

      <section className="welcome-features" aria-label="How SwiftChart works">
        {features.map(([Icon, title, body]) => (
          <article key={title}>
            <Icon size={18} aria-hidden="true" />
            <h2>{title}</h2>
            <p>{body}</p>
          </article>
        ))}
      </section>

      <footer className="welcome-footer">Signals are analysis, not financial advice. Trade at your own risk.</footer>
    </main>
  );
}
