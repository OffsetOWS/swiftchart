import { ArrowRight, Bell, CandlestickChart, ShieldCheck, TrendingUp } from "lucide-react";

function MiniCandle({ type = "up", height = 76 }) {
  return (
    <span className={`mini-candle ${type}`} style={{ "--h": `${height}px` }}>
      <i />
    </span>
  );
}

export default function Hero() {
  return (
    <section id="top" className="hero-section">
      <div className="hero-copy">
        <span className="eyebrow">AI trading analysis for crypto and forex</span>
        <h1>Trade Smarter With SwiftChart</h1>
        <p>
          AI-powered chart analysis, trade setups, support/resistance mapping, and Telegram alerts for crypto and forex traders.
        </p>
        <div className="hero-actions">
          <a className="button primary-button" href="#telegram">
            Start on Telegram <ArrowRight size={18} />
          </a>
          <a className="button secondary-button" href="#features">
            View Features
          </a>
        </div>
      </div>

      <div className="hero-visual" aria-label="SwiftChart product preview">
        <div className="mockup-topbar">
          <span />
          <span />
          <span />
          <b>SOLUSDT / 4H</b>
        </div>
        <div className="mockup-chart">
          <div className="zone zone-resistance">Resistance</div>
          <div className="zone zone-support">Support</div>
          <div className="candle-row">
            <MiniCandle type="up" height={74} />
            <MiniCandle type="down" height={52} />
            <MiniCandle type="up" height={92} />
            <MiniCandle type="up" height={64} />
            <MiniCandle type="down" height={86} />
            <MiniCandle type="up" height={116} />
            <MiniCandle type="down" height={68} />
          </div>
          <svg viewBox="0 0 520 220" className="chart-line-art" role="img" aria-label="Price structure line">
            <path d="M24 160 C92 116 122 126 170 92 S256 54 316 88 S390 158 492 72" />
          </svg>
        </div>
        <div className="signal-card">
          <div>
            <span>Potential setup</span>
            <strong>Long pullback</strong>
          </div>
          <div className="score-ring">78</div>
        </div>
        <div className="mockup-metrics">
          <div><TrendingUp size={18} /><span>HTF Bias</span><b>Bullish</b></div>
          <div><ShieldCheck size={18} /><span>Risk/Reward</span><b>2.7R</b></div>
          <div><Bell size={18} /><span>Alert</span><b>Telegram</b></div>
          <div><CandlestickChart size={18} /><span>Regime</span><b>Range</b></div>
        </div>
      </div>
    </section>
  );
}
