import { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { Analytics, track } from "@vercel/analytics/react";
import Dashboard from "./pages/Dashboard.jsx";
import Analysis from "./pages/Analysis.jsx";
import TradeHistory from "./pages/TradeHistory.jsx";
import Auth from "./pages/Auth.jsx";
import Landing from "./pages/Landing.jsx";
import LaunchFlow from "./pages/LaunchFlow.jsx";
import { createPaperTrade, getAnalysis, getCandles, getTopIdeas } from "./lib/api.js";
import swiftChartLogo from "./assets/swiftchart-logo.png";
import "./styles/global.css";

const HYPERLIQUID_REF_URL = "https://app.hyperliquid.xyz/join/OFFSET";
const TELEGRAM_BOT_URL = import.meta.env.VITE_TELEGRAM_BOT_URL || "https://t.me/SwiftChartBot";

function trackEvent(name, properties = {}) {
  track(name, {
    app: "swiftchart",
    ...properties,
  });
}

export default function App() {
  const isLandingPage = window.location.pathname === "/";
  const isLaunchPage = window.location.pathname === "/launch";
  const isAuthPage = window.location.pathname === "/auth" || window.location.pathname === "/login" || window.location.pathname === "/signup";
  const [page, setPage] = useState("dashboard");
  const [nightMode, setNightMode] = useState(true);
  const [clock, setClock] = useState("");
  const [exchange, setExchange] = useState("all");
  const [timeframe, setTimeframe] = useState("4h");
  const [symbol, setSymbol] = useState("SOLUSDT");
  const [risk, setRisk] = useState({ accountSize: 10000, riskPerTrade: 1, minRR: 2, maxOpenTrades: 3 });
  const [topIdeas, setTopIdeas] = useState([]);
  const [candles, setCandles] = useState([]);
  const [analysis, setAnalysis] = useState(null);
  const [loading, setLoading] = useState(false);
  const [loadingTopIdeas, setLoadingTopIdeas] = useState(false);
  const [notice, setNotice] = useState("");

  async function refreshTopIdeas() {
    setLoadingTopIdeas(true);
    setNotice("");
    try {
      const data = await getTopIdeas({ exchange, timeframe });
      setTopIdeas(data.ideas || []);
    } catch (error) {
      setNotice(error.message);
    } finally {
      setLoadingTopIdeas(false);
    }
  }

  async function runAnalysis() {
    setLoading(true);
    setNotice("");
    try {
      const [candleData, analysisData] = await Promise.all([
        getCandles({ exchange, symbol, timeframe }),
        getAnalysis({ exchange, symbol, timeframe, risk }),
      ]);
      setCandles(candleData);
      setAnalysis(analysisData);
    } catch (error) {
      setNotice(error.message);
    } finally {
      setLoading(false);
    }
  }

  async function paperTrade(idea) {
    trackEvent("clicked_connect_wallet", {
      source: "paper_trade",
      symbol: idea.symbol,
      timeframe: idea.timeframe,
      direction: idea.direction,
    });
    window.open(HYPERLIQUID_REF_URL, "_blank", "noopener,noreferrer");
    const entry = (idea.entry_zone[0] + idea.entry_zone[1]) / 2;
    await createPaperTrade({
      symbol: idea.symbol,
      timeframe: idea.timeframe,
      exchange: idea.exchange,
      direction: idea.direction,
      entry_price: entry,
      stop_loss: idea.stop_loss,
      take_profit_1: idea.take_profit_1,
      take_profit_2: idea.take_profit_2,
      size: idea.position_size_units || 0,
      notes: idea.reason,
    });
    setNotice("Paper trade saved.");
  }

  function openPage(nextPage) {
    setPage(nextPage);
    if (nextPage === "dashboard") {
      trackEvent("opened_dashboard");
    }
    if (nextPage === "ideas" || nextPage === "markets") {
      trackEvent("viewed_signal_page", { page: nextPage === "ideas" ? "trade_ideas" : "markets" });
    }
  }

  useEffect(() => {
    refreshTopIdeas();
  }, [exchange, timeframe]);

  useEffect(() => {
    runAnalysis();
  }, []);

  useEffect(() => {
    trackEvent("page_visit", { page });
    if (page === "dashboard") {
      trackEvent("opened_dashboard", { source: "page_visit" });
    }
    if (page === "ideas" || page === "markets") {
      trackEvent("viewed_signal_page", { page: page === "ideas" ? "trade_ideas" : "markets", source: "page_visit" });
    }
  }, [page]);

  useEffect(() => {
    function tick() {
      setClock(new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }).replace(":", " : "));
    }
    tick();
    const timer = window.setInterval(tick, 1000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    function updateCursor(event) {
      document.documentElement.style.setProperty("--cursor-x", `${event.clientX}px`);
      document.documentElement.style.setProperty("--cursor-y", `${event.clientY}px`);
    }
    window.addEventListener("pointermove", updateCursor);
    return () => window.removeEventListener("pointermove", updateCursor);
  }, []);

  const tabs = [
    ["dashboard", "Dashboard"],
    ["markets", "Markets"],
    ["ideas", "Trade Ideas"],
    ["history", "History"],
    ["alerts", "Alerts"],
    ["settings", "Settings"],
  ];

  if (isLandingPage) {
    return (
      <>
        <Landing />
        <Analytics />
      </>
    );
  }

  if (isLaunchPage) {
    return (
      <>
        <LaunchFlow />
        <Analytics />
      </>
    );
  }

  if (isAuthPage) {
    return (
      <>
        <Auth />
        <Analytics />
      </>
    );
  }

  return (
    <>
    <main className={nightMode ? "app-shell dark-mode" : "app-shell"}>
      <div className="grain" />
      <div className="cursor-aura" />

      <section className="landing-stage" aria-label="SwiftChart terminal introduction">
        <header className="reference-header">
          <div />
          <div className="theme-control">
            <span>{nightMode ? "Night" : "Day"}</span>
            <button className={nightMode ? "theme-toggle on" : "theme-toggle"} onClick={() => setNightMode((value) => !value)} aria-label="Toggle night and day theme" aria-pressed={nightMode}>
              <i />
            </button>
          </div>
        </header>

        <div className="terminal-hero">
          <div className="hero-logo-shell" aria-label="SwiftChart logo">
            <img src={swiftChartLogo} alt="SwiftChart" className="hero-logo-image" />
          </div>
        </div>

        <div className="stage-footer">
          <div className="footer-menu-wrap">
            <nav className="side-menu footer-menu" aria-label="Site menu">
              {["About us", "Contacts", "FAQ"].map((item) => (
                <a key={item} href={`#${item.toLowerCase().replaceAll(" ", "-")}`}>
                  <span className="menu-dot" />
                  {item}
                </a>
              ))}
            </nav>
            <p>// AI-powered market analysis across crypto</p>
          </div>
          <button onClick={() => document.getElementById("terminal-workspace")?.scrollIntoView({ behavior: "smooth" })}>Scroll Down ■</button>
          <p>{clock || "10 : 22 pm"}</p>
        </div>
      </section>

      <section id="terminal-workspace" className="terminal-workspace">
        <div className="workspace-intro">
          <span>SwiftChart</span>
          <h1>Mysterious market structure, made readable.</h1>
          <p>Top trade ideas, range context, chart analysis, alerts, and trade memory stay intact below the quiet terminal shell.</p>
        </div>

        <nav className="nav" aria-label="SwiftChart sections">
          {tabs.map(([key, label]) => (
            <button key={key} className={page === key ? "active" : ""} onClick={() => openPage(key)}>
              <span />
              {label}
            </button>
          ))}
        </nav>

        {notice ? <div className="risk-strip">{notice}</div> : null}

        <div className="tab-stage" key={page}>
          {page === "dashboard" && (
            <Dashboard
              exchange={exchange}
              setExchange={setExchange}
              timeframe={timeframe}
              setTimeframe={setTimeframe}
              topIdeas={topIdeas}
              loadingTopIdeas={loadingTopIdeas}
              refreshTopIdeas={refreshTopIdeas}
            />
          )}
          {page === "ideas" && (
            <Dashboard
              exchange={exchange}
              setExchange={setExchange}
              timeframe={timeframe}
              setTimeframe={setTimeframe}
              topIdeas={topIdeas}
              loadingTopIdeas={loadingTopIdeas}
              refreshTopIdeas={refreshTopIdeas}
              compact
            />
          )}
          {page === "markets" && (
            <Analysis
              state={{ symbol, exchange, timeframe, risk }}
              setters={{ setSymbol, setExchange, setTimeframe, setRisk }}
              candles={candles}
              analysis={analysis}
              loading={loading}
              onAnalyze={runAnalysis}
              onPaperTrade={paperTrade}
            />
          )}
          {page === "history" && <TradeHistory />}
          {page === "alerts" && (
            <section className="panel terminal-note" id="contacts">
              <span className="eyebrow">ALERT RELAY</span>
              <h2>Telegram waits for clean setups.</h2>
              <p>SwiftChart can notify subscribed Telegram users when the scanner finds valid trade ideas that clear the strategy threshold.</p>
              <a
                className="telegram-link"
                href={TELEGRAM_BOT_URL}
                target="_blank"
                rel="noreferrer"
                onClick={() => trackEvent("clicked_telegram_bot", { source: "alerts_page" })}
              >
                Open SwiftChart on Telegram
              </a>
              <div className="mono-list">
                <span>/subscribe</span><span>/alerts</span><span>/top</span><span>/checktrades</span>
              </div>
            </section>
          )}
          {page === "settings" && (
            <section className="panel terminal-note" id="faq">
              <span className="eyebrow">CONTROL ROOM</span>
              <h2>Risk remains first.</h2>
              <p>SwiftChart keeps analysis, alerts, history, and risk context focused here while trade control stays on Telegram.</p>
            </section>
          )}
        </div>
      </section>
    </main>
    <Analytics />
    </>
  );
}

createRoot(document.getElementById("root")).render(<App />);
