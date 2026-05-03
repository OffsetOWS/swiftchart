import {
  BellRing,
  BrainCircuit,
  Crosshair,
  Gauge,
  Layers3,
  Radar,
  Send,
  ShieldCheck,
} from "lucide-react";
import FAQ from "./FAQ.jsx";
import FeatureCard from "./FeatureCard.jsx";
import Footer from "./Footer.jsx";
import Hero from "./Hero.jsx";
import HowItWorks from "./HowItWorks.jsx";
import Navbar from "./Navbar.jsx";
import PricingCard from "./PricingCard.jsx";

const trustItems = [
  "Built for crypto and forex traders",
  "Telegram-first",
  "Fast chart analysis",
  "Risk-focused setups",
];

const features = [
  [BrainCircuit, "AI Chart Analysis", "Get clean reads on market regime, higher-timeframe bias, momentum, and trade quality."],
  [Layers3, "Support & Resistance Mapping", "SwiftChart clusters swing reactions into scored zones instead of random single lines."],
  [Crosshair, "Trade Setup Detection", "Only valid setups with structure, risk/reward, and confirmation make it through."],
  [BellRing, "Telegram Bot Alerts", "Request analysis or top ideas from Telegram without opening a dashboard."],
  [ShieldCheck, "Risk/Reward Breakdown", "Every idea includes entry, stop, targets, invalidation, and a clear risk warning."],
  [Radar, "Multi-pair Tracking", "Scan popular liquid pairs and return only the best available setups."],
];

const strategyItems = [
  "Market structure",
  "Key zones",
  "Breakouts",
  "Pullbacks",
  "Liquidity areas",
  "Risk management",
];

const plans = [
  {
    name: "Free Trial",
    price: "$0",
    description: "Explore SwiftChart analysis and Telegram workflows.",
    features: ["Basic chart analysis", "Telegram bot access", "Risk-first output"],
  },
  {
    name: "Pro Plan",
    price: "$29/mo",
    description: "For active traders who want faster analysis and more coverage.",
    features: ["Top trade idea scanner", "Multi-pair tracking", "Priority Telegram alerts"],
    featured: true,
  },
  {
    name: "Custom Plan",
    price: "Custom",
    description: "For teams, communities, and private trading groups.",
    features: ["Custom watchlists", "Workflow support", "Private deployment options"],
  },
];

function ProductPreview({ topIdeas, loadingTopIdeas, refreshTopIdeas }) {
  const topIdea = topIdeas?.[0];
  return (
    <section className="section product-preview" id="features">
      <div className="section-heading">
        <span className="eyebrow">Features</span>
        <h2>Professional analysis without the clutter.</h2>
        <p>SwiftChart turns complex price action into a score, a grade, and a trade plan you can actually read.</p>
      </div>

      <div className="features-grid">
        {features.map(([Icon, title, description]) => (
          <FeatureCard key={title} icon={Icon} title={title} description={description} />
        ))}
      </div>

      <div className="live-preview">
        <div className="preview-chart-card">
          <div className="preview-card-head">
            <div>
              <span className="eyebrow">Live product preview</span>
              <h3>Top setup scanner</h3>
            </div>
            <button onClick={refreshTopIdeas}>{loadingTopIdeas ? "Scanning..." : "Refresh"}</button>
          </div>
          <div className="preview-chart">
            <span className="preview-line support" />
            <span className="preview-line resistance" />
            <svg viewBox="0 0 700 240" aria-hidden="true">
              <path d="M20 175 C90 120 120 136 170 100 C225 58 288 80 330 114 C386 158 438 178 492 128 C546 78 610 92 680 48" />
            </svg>
            <div className="chart-label one">Support zone</div>
            <div className="chart-label two">Liquidity sweep</div>
          </div>
        </div>
        <div className="preview-signal-card">
          <span className="eyebrow">Current idea</span>
          <h3>{topIdea ? `${topIdea.symbol} ${topIdea.direction}` : "No forced signal"}</h3>
          <p>{topIdea ? topIdea.reason : "If the market is choppy, mid-range, or below score threshold, SwiftChart returns no trade."}</p>
          <div className="signal-metrics">
            <div><span>Score</span><b>{topIdea?.setup_score ?? "-"}{topIdea ? "/100" : ""}</b></div>
            <div><span>R:R</span><b>{topIdea?.risk_reward_ratio ?? "-"}</b></div>
            <div><span>Regime</span><b>{topIdea?.market_regime ?? "No trade"}</b></div>
            <div><span>HTF Bias</span><b>{topIdea?.higher_timeframe_bias ?? "Neutral"}</b></div>
          </div>
        </div>
      </div>
    </section>
  );
}

function StrategySection() {
  return (
    <section id="strategy" className="section strategy-section">
      <div className="strategy-copy">
        <span className="eyebrow">Strategy engine</span>
        <h2>Designed to protect capital before chasing signals.</h2>
        <p>
          SwiftChart analyzes market structure, key zones, breakouts, pullbacks, liquidity areas, and risk management before it shows any trade idea.
        </p>
      </div>
      <div className="strategy-grid">
        {strategyItems.map((item) => (
          <div key={item} className="strategy-pill">
            <Gauge size={18} /> {item}
          </div>
        ))}
      </div>
    </section>
  );
}

function TelegramSection() {
  return (
    <section id="telegram" className="telegram-section">
      <div>
        <span className="eyebrow">Telegram-first</span>
        <h2>Run analysis from the app already on your phone.</h2>
        <p>Launch SwiftChart Bot, request a pair, and receive market regime, zones, score, risk/reward, and invalidation in a clean message.</p>
      </div>
      <a className="button primary-button" href="#" aria-label="Launch Telegram Bot placeholder">
        <Send size={18} /> Launch Telegram Bot
      </a>
    </section>
  );
}

function PricingSection() {
  return (
    <section id="pricing" className="section pricing-section">
      <div className="section-heading">
        <span className="eyebrow">Access</span>
        <h2>Simple plans for traders and teams.</h2>
        <p>Pricing is intentionally simple while SwiftChart is in active product buildout.</p>
      </div>
      <div className="pricing-grid">
        {plans.map((plan) => <PricingCard key={plan.name} {...plan} />)}
      </div>
    </section>
  );
}

export default function LandingPage({ topIdeas, loadingTopIdeas, refreshTopIdeas }) {
  return (
    <>
      <Navbar />
      <main>
        <Hero />
        <section className="trust-bar" aria-label="SwiftChart trust signals">
          {trustItems.map((item) => <span key={item}>{item}</span>)}
        </section>
        <ProductPreview topIdeas={topIdeas} loadingTopIdeas={loadingTopIdeas} refreshTopIdeas={refreshTopIdeas} />
        <HowItWorks />
        <StrategySection />
        <TelegramSection />
        <PricingSection />
        <FAQ />
      </main>
      <Footer />
    </>
  );
}
