import { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { Analytics, track } from "@vercel/analytics/react";
import Dashboard from "./pages/Dashboard.jsx";
import Analysis from "./pages/Analysis.jsx";
import TradeHistory from "./pages/TradeHistory.jsx";
import Auth from "./pages/Auth.jsx";
import Docs from "./pages/Docs.jsx";
import Landing from "./pages/Landing.jsx";
import LaunchFlow from "./pages/LaunchFlow.jsx";
import { AuthProvider, useAuth } from "./lib/AuthContext.jsx";
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
  const auth = useAuth();
  const [path, setPath] = useState(window.location.pathname);
  const isLandingPage = path === "/";
  const isLaunchPage = path === "/launch";
  const isAppPage = path === "/app";
  const isAuthPage = path === "/auth" || path === "/login" || path === "/signup";
  const isDocsPage = path === "/docs" || path.startsWith("/docs/");
  const [page, setPage] = useState("dashboard");
  const [nightMode, setNightMode] = useState(true);
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

  function navigate(nextPath, { replace = false } = {}) {
    if (window.location.pathname !== nextPath) {
      const method = replace ? "replaceState" : "pushState";
      window.history[method]({}, "", nextPath);
    }
    setPath(nextPath);
  }

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
    function syncPath() {
      setPath(window.location.pathname);
    }
    window.addEventListener("popstate", syncPath);
    return () => window.removeEventListener("popstate", syncPath);
  }, []);

  useEffect(() => {
    if (auth.loading) return;
    if (isAppPage && !auth.isAuthenticated) {
      navigate("/launch", { replace: true });
    }
    if (isAuthPage && auth.isAuthenticated) {
      navigate("/app", { replace: true });
    }
    if (isLaunchPage && auth.isAuthenticated) {
      navigate("/app", { replace: true });
    }
  }, [auth.loading, auth.isAuthenticated, isAppPage, isAuthPage, isLaunchPage]);

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
  ];

  const nav = (
    <nav className={isAppPage ? "nav app-nav" : "nav"} aria-label="SwiftChart sections">
      {tabs.map(([key, label]) => (
        <button key={key} className={page === key ? "active" : ""} onClick={() => openPage(key)}>
          <span />
          {label}
        </button>
      ))}
    </nav>
  );

  const themeControl = (
    <div className="theme-control">
      <span>{nightMode ? "Night" : "Day"}</span>
      <button className={nightMode ? "theme-toggle on" : "theme-toggle"} onClick={() => setNightMode((value) => !value)} aria-label="Toggle night and day theme" aria-pressed={nightMode}>
        <i />
      </button>
    </div>
  );

  const userMenu = auth.profile || auth.user ? (
    <div className="user-menu" aria-label="SwiftChart profile">
      <img src={auth.profile?.avatar_url || auth.user?.user_metadata?.avatar_url || auth.user?.user_metadata?.picture || swiftChartLogo} alt="" />
      <div>
        <span>{auth.profile?.username || "SwiftChart user"}</span>
        <small>{auth.profileLoading ? "Syncing profile" : auth.profile?.profile_storage_ready === false ? "Session active" : "Signed in"}</small>
      </div>
      <button
        type="button"
        onClick={async () => {
          await auth.signOut();
          navigate("/launch", { replace: true });
        }}
      >
        Logout
      </button>
    </div>
  ) : null;

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

  if (isDocsPage) {
    return (
      <>
        <Docs />
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

  if (isAppPage && (auth.loading || auth.profileLoading)) {
    return (
      <>
        <AuthLoading />
        <Analytics />
      </>
    );
  }

  if (isAppPage && !auth.isAuthenticated) {
    return (
      <>
        <LaunchFlow />
        <Analytics />
      </>
    );
  }

  return (
    <>
    <main className={`${nightMode ? "app-shell dark-mode" : "app-shell"}${isAppPage ? " app-view" : ""}`}>
      <div className="grain" />
      <div className="cursor-aura" />

      {!isAppPage ? (
        <section className="landing-stage" aria-label="SwiftChart terminal introduction">
          <header className="reference-header">
            <div />
            {themeControl}
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
      ) : null}

      <section id="terminal-workspace" className="terminal-workspace">
        {!isAppPage ? (
          <div className="workspace-intro">
            <span>SwiftChart</span>
            <h1>Mysterious market structure, made readable.</h1>
            <p>Top trade ideas, range context, chart analysis, alerts, and trade memory stay intact below the quiet terminal shell.</p>
          </div>
        ) : null}

        {isAppPage ? (
          <div className="app-top-controls">
            {nav}
            <div className="app-theme-control">{themeControl}</div>
            {userMenu}
          </div>
        ) : nav}

        {auth.error ? <div className="risk-strip">{auth.error}</div> : null}
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
        </div>
      </section>
    </main>
    <Analytics />
    </>
  );
}

function AuthLoading() {
  return (
    <main className="auth-shell">
      <section className="auth-card auth-loading-card" aria-live="polite">
        <div className="launch-mark" aria-hidden="true">
          <img src={swiftChartLogo} alt="" />
        </div>
        <div className="auth-copy">
          <span className="eyebrow">SwiftChart account</span>
          <h1>Restoring session</h1>
          <p>Loading your dashboard, profile, and saved SwiftChart access.</p>
        </div>
        <div className="launch-progress">
          <span />
        </div>
      </section>
    </main>
  );
}

createRoot(document.getElementById("root")).render(
  <AuthProvider>
    <App />
  </AuthProvider>
);
