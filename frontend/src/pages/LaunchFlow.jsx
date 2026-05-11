import { Check, Chrome, Circle, Dot, Lock, TrendingDown, TrendingUp, Zap } from "lucide-react";
import { useEffect, useState } from "react";

const loadingSteps = [
  "Connecting to market data",
  "Loading market structure",
  "Analyzing live bias",
  "Fetching latest signals",
  "Preparing your dashboard",
];

const navLinks = [
  ["Docs", "#docs"],
  ["X / Twitter", "#twitter"],
  ["Telegram", "https://t.me/SwiftChartBot"],
  ["Discord", "#discord"],
];

const signals = [
  { symbol: "BTCUSDT", direction: "Short", detail: "Bearish structure active", score: "82%" },
  { symbol: "ETHUSDT", direction: "Wait", detail: "Reclaim not confirmed", score: "64%" },
  { symbol: "SOLUSDT", direction: "Long", detail: "Reversal watch only", score: "71%" },
];

export default function LaunchFlow() {
  const [step, setStep] = useState(0);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    const stepTimer = window.setInterval(() => {
      setStep((current) => Math.min(current + 1, loadingSteps.length - 1));
    }, 650);
    const readyTimer = window.setTimeout(() => setReady(true), 3600);
    return () => {
      window.clearInterval(stepTimer);
      window.clearTimeout(readyTimer);
    };
  }, []);

  return (
    <main className={ready ? "launch-flow ready" : "launch-flow"}>
      <section className="launch-loading" aria-hidden={ready}>
        <header className="launch-loading-nav">
          <a href="/" className="launch-loading-brand" aria-label="SwiftChart home">
            <Zap size={28} fill="currentColor" />
            <span>SwiftChart</span>
          </a>
          <nav aria-label="SwiftChart links">
            {navLinks.map(([label, href]) => (
              <a key={label} href={href} target={href.startsWith("http") ? "_blank" : undefined} rel={href.startsWith("http") ? "noreferrer" : undefined}>
                {label}
              </a>
            ))}
          </nav>
        </header>

        <div className="launch-initializer">
          <div className="launch-mark" aria-hidden="true">
            <Zap size={34} fill="currentColor" />
          </div>
          <h1>Initializing SwiftChart</h1>

          <ul className="launch-step-list" aria-label="SwiftChart initialization steps">
            {loadingSteps.map((label, index) => {
              const complete = index < step;
              const active = index === step;
              return (
                <li key={label} className={complete ? "complete" : active ? "active" : ""}>
                  <span>
                    {complete ? <Check size={14} strokeWidth={3} /> : active ? <Dot size={26} strokeWidth={4} /> : <Circle size={20} strokeWidth={2} />}
                  </span>
                  {label}
                </li>
              );
            })}
          </ul>

          <div className="launch-progress-row">
            <div className="launch-progress" aria-label="Loading progress">
              <span style={{ width: `${Math.min(96, 16 + step * 20)}%` }} />
            </div>
            <b>{Math.min(96, 16 + step * 20)}%</b>
          </div>
        </div>
      </section>

      <section className="launch-preview" aria-label="SwiftChart preview">
        <div className="launch-preview-grid">
          <article className="launch-preview-card bias-card">
            <span className="eyebrow">Live Bias</span>
            <div className="bias-row">
              <TrendingDown size={26} />
              <h2>Bearish</h2>
            </div>
            <p>Structure break confirmed. Long setups require reclaim.</p>
          </article>

          <article className="launch-preview-card confidence-card">
            <span>Confidence</span>
            <b>84%</b>
            <div className="confidence-meter">
              <i />
            </div>
          </article>

          <article className="launch-preview-card locked-card">
            <div>
              <span className="eyebrow">Market Structure</span>
              <h3>Lower high / lower low detected</h3>
            </div>
            <Lock size={20} />
          </article>

          <article className="launch-preview-card signal-card">
            <div className="panel-head">
              <div>
                <span className="eyebrow">Recent Signals</span>
                <h3>Signal history</h3>
              </div>
              <Lock size={18} />
            </div>
            <div className="launch-signal-list">
              {signals.map((signal) => (
                <div key={signal.symbol} className={signal.direction.toLowerCase()}>
                  <span>{signal.symbol}</span>
                  <b>{signal.direction}</b>
                  <small>{signal.detail}</small>
                  <em>{signal.score}</em>
                </div>
              ))}
            </div>
          </article>

          <article className="launch-preview-card mini-preview locked-soft">
            <TrendingUp size={20} />
            <span>Watchlist soon</span>
          </article>

          <article className="launch-preview-card mini-preview locked-soft">
            <Lock size={20} />
            <span>Telegram alerts soon</span>
          </article>
        </div>

        <section className="unlock-card" aria-labelledby="unlock-title">
          <span className="eyebrow">SwiftChart account</span>
          <h1 id="unlock-title">Unlock Full Access</h1>
          <p>Sign in to access live market bias, signal history, and future alerts.</p>
          <a className="google-auth-button" href="/app">
            <Chrome size={18} aria-hidden="true" />
            <span>Continue with Google</span>
          </a>
        </section>
      </section>
    </main>
  );
}
