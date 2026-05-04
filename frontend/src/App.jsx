import { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import Dashboard from "./pages/Dashboard.jsx";
import Analysis from "./pages/Analysis.jsx";
import TradeHistory from "./pages/TradeHistory.jsx";
import { createPaperTrade, getAnalysis, getCandles, getTopIdeas } from "./lib/api.js";
import "./styles/global.css";

export default function App() {
  const [page, setPage] = useState("dashboard");
  const [sound, setSound] = useState(false);
  const [nightMode, setNightMode] = useState(false);
  const [clock, setClock] = useState("");
  const [exchange, setExchange] = useState("hyperliquid");
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

  useEffect(() => {
    refreshTopIdeas();
  }, [exchange, timeframe]);

  useEffect(() => {
    runAnalysis();
  }, []);

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

  return (
    <main className={nightMode ? "app-shell dark-mode" : "app-shell"}>
      <div className="grain" />
      <div className="cursor-aura" />

      <section className="landing-stage" aria-label="SwiftChart terminal introduction">
        <header className="reference-header">
          <nav className="side-menu" aria-label="Site menu">
            {["About us", "Contacts", "FAQ"].map((item) => (
              <a key={item} href={`#${item.toLowerCase().replaceAll(" ", "-")}`}>
                <span className="menu-dot" />
                {item}
              </a>
            ))}
          </nav>
          <button className="center-logo" onClick={() => setPage("dashboard")} aria-label="SwiftChart home">
            <span>S</span><span>C</span>
          </button>
          <div className="sound-control">
            <span>Sound</span>
            <button className={sound ? "sound-toggle on" : "sound-toggle"} onClick={() => setSound((value) => !value)} aria-pressed={sound}>
              <i />
            </button>
          </div>
        </header>

        <div className="terminal-hero">
          <div className="retro-terminal" aria-label="SwiftChart terminal preview">
            <div className="terminal-screen">
              <div className="screen-noise" />
              <div className="screen-copy">
                <b>SwiftChart</b>
                <span>{symbol} / {timeframe} / {exchange}</span>
              </div>
              <div className="mini-terminal-chart">
                <i /><i /><i /><i /><i /><i />
              </div>
              <div className="screen-grid">
                <span>RANGE</span>
                <span>SWEEP</span>
                <span>NO MID</span>
                <span>{topIdeas[0]?.symbol || "BTCUSDT"}</span>
              </div>
            </div>
            <div className="terminal-body">
              <span /><span /><span />
              <div className="vent" />
            </div>
          </div>
          <button className="switch-caption" onClick={() => setNightMode((value) => !value)} aria-pressed={nightMode}>
            <svg viewBox="0 0 104 76" role="img">
              <path d="M88 62 C18 52 10 6 52 4" />
              <path d="M82 57 L91 63 L82 68" />
            </svg>
            <span>{nightMode ? "Switch Night 'N' Day" : "Switch Day 'N' Night"}</span>
          </button>
        </div>

        <div className="stage-footer">
          <p>// AI-powered market analysis across crypto</p>
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
            <button key={key} className={page === key ? "active" : ""} onClick={() => setPage(key)}>
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
              <div className="mono-list">
                <span>/subscribe</span><span>/alerts</span><span>/top</span><span>/checktrades</span>
              </div>
            </section>
          )}
          {page === "settings" && (
            <section className="panel terminal-note" id="faq">
              <span className="eyebrow">CONTROL ROOM</span>
              <h2>Risk remains first.</h2>
              <p>Default settings keep paper mode enabled, minimum R:R at 2.0, and live execution disabled unless explicitly configured outside the frontend.</p>
            </section>
          )}
        </div>
      </section>
    </main>
  );
}

createRoot(document.getElementById("root")).render(<App />);
