import { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { Analytics, track } from "@vercel/analytics/react";
import Dashboard from "./pages/Dashboard.jsx";
import Analysis from "./pages/Analysis.jsx";
import Forex from "./pages/Forex.jsx";
import TradeHistory from "./pages/TradeHistory.jsx";
import Watchlist from "./pages/Watchlist.jsx";
import MobileDemo from "./components/MobileDemo.jsx";
import DesktopMobileGate from "./components/DesktopMobileGate.jsx";
import AdminPayments from "./pages/AdminPayments.jsx";
import Auth from "./pages/Auth.jsx";
import Docs from "./pages/Docs.jsx";
import Landing from "./pages/Landing.jsx";
import LaunchFlow from "./pages/LaunchFlow.jsx";
import { AuthProvider, useAuth } from "./lib/AuthContext.jsx";
import { PAYMENTS_COMING_SOON_MESSAGE, PAYMENTS_ENABLED } from "./lib/featureFlags.js";
import { getAnalysis, getCandles, getTopIdeas, refreshTopIdeasCache } from "./lib/api.js";
import { createPaperTradeFromSignal, listPaperTradesForSignals, signalIdForIdea } from "./lib/paperTrades.js";
import { freshnessForIdea, liquidityForIdea } from "./lib/signalQuality.js";
import swiftChartLogo from "./assets/swiftchart-logo.png";
import "./styles/global.css";

const TELEGRAM_BOT_URL = import.meta.env.VITE_TELEGRAM_BOT_URL || "https://t.me/SwiftChartBot";
const HYPERLIQUID_REFERRAL_URL = import.meta.env.VITE_HYPERLIQUID_REFERRAL_URL || "";

function trackEvent(name, properties = {}) {
  track(name, {
    app: "swiftchart",
    ...properties,
  });
}

function analysisSymbolFromPath(pathname) {
  const match = String(pathname || "").match(/^\/analysis\/([^/]+)$/i);
  if (!match) return "";
  const base = decodeURIComponent(match[1]).replace(/[^a-z0-9]/gi, "").toUpperCase();
  return base ? `${base.replace(/(USDT|USDC|USD|PERP)$/i, "")}USDT` : "";
}

function appTabFromPath(pathname) {
  const match = String(pathname || "").match(/^\/app\/(home|scan|history|account|notifications)$/i);
  return match ? match[1].toLowerCase() : "home";
}

function safeReturnTo(value, fallback = "/app/home") {
  return value?.startsWith("/") && !value.startsWith("//") ? value : fallback;
}

export default function App() {
  const auth = useAuth();
  const [path, setPath] = useState(window.location.pathname);
  const isLandingPage = path === "/";
  const isLaunchPage = path === "/launch";
  const isAppPage = path === "/app" || /^\/app\/(home|scan|history|account|notifications)$/.test(path);
  const isAnalysisPage = path.startsWith("/analysis/");
  const hasWorkspaceChrome = isAppPage || isAnalysisPage;
  const isMobileDemoPage = path === "/mobile-demo";
  const isAdminPaymentsPage = path === "/admin/payments";
  const isAuthPage = ["/auth", "/login", "/signup", "/forgot-password", "/reset-password"].includes(path);
  const isCredentialEntryPage = ["/auth", "/login", "/signup"].includes(path);
  const isProtectedPage = isAppPage || isMobileDemoPage || isAdminPaymentsPage;
  const isDocsPage = path === "/docs" || path.startsWith("/docs/");
  const [page, setPage] = useState(isAnalysisPage ? "markets" : "dashboard");
  const [nightMode, setNightMode] = useState(true);
  const [clock, setClock] = useState("");
  const [exchange, setExchange] = useState("all");
  const [timeframe, setTimeframe] = useState("4h");
  const [symbol, setSymbol] = useState(analysisSymbolFromPath(window.location.pathname) || "SOLUSDT");
  const [risk, setRisk] = useState({ accountSize: 10000, riskPerTrade: 1, minRR: 2, maxOpenTrades: 3 });
  const [topIdeas, setTopIdeas] = useState([]);
  const [pendingSetups, setPendingSetups] = useState([]);
  const [topIdeasMeta, setTopIdeasMeta] = useState({});
  const [candles, setCandles] = useState([]);
  const [analysis, setAnalysis] = useState(null);
  const [analysisError, setAnalysisError] = useState("");
  const [loading, setLoading] = useState(false);
  const [loadingTopIdeas, setLoadingTopIdeas] = useState(false);
  const [notice, setNotice] = useState("");
  const [noticeType, setNoticeType] = useState("info");
  const [takenSignalIds, setTakenSignalIds] = useState(new Set());
  const [paperTradeLoadingSignalId, setPaperTradeLoadingSignalId] = useState("");
  const [paperHistoryVersion, setPaperHistoryVersion] = useState(0);

  function navigate(nextPath, { replace = false } = {}) {
    const nextUrl = new URL(nextPath, window.location.origin);
    if (`${window.location.pathname}${window.location.search}` !== `${nextUrl.pathname}${nextUrl.search}`) {
      const method = replace ? "replaceState" : "pushState";
      window.history[method]({}, "", `${nextUrl.pathname}${nextUrl.search}`);
    }
    setPath(nextUrl.pathname);
  }

  async function refreshTopIdeas(options = {}) {
    const manual = options?.manual === true;
    const firstLoad = topIdeas.length === 0 && pendingSetups.length === 0;
    setLoadingTopIdeas(firstLoad);
    setNotice("");
    setNoticeType("info");
    try {
      if (manual) {
        await refreshTopIdeasCache({ exchange, timeframe });
      }
      const data = await getTopIdeas({ exchange, timeframe });
      setTopIdeas(data.ideas || []);
      setPendingSetups(data.pending_setups || []);
      setTopIdeasMeta({
        refreshing: Boolean(data.refreshing || data.refresh_in_progress),
        cacheAgeSeconds: data.cache_age_seconds,
        lastRefreshStartedAt: data.last_refresh_started_at,
        lastRefreshFinishedAt: data.last_refresh_finished_at,
        scanDurationSeconds: data.scan_duration_seconds,
      });
    } catch (error) {
      setNoticeType("error");
      setNotice(error.message);
    } finally {
      setLoadingTopIdeas(false);
    }
  }

  async function runAnalysis(overrides = {}) {
    const requestExchange = overrides.exchange || exchange;
    const requestSymbol = overrides.symbol || symbol;
    const requestTimeframe = overrides.timeframe || timeframe;
    setLoading(true);
    setNotice("");
    setNoticeType("info");
    setAnalysisError("");
    try {
      const [candleData, analysisData] = await Promise.all([
        getCandles({ exchange: requestExchange, symbol: requestSymbol, timeframe: requestTimeframe }),
        getAnalysis({ exchange: requestExchange, symbol: requestSymbol, timeframe: requestTimeframe, risk }),
      ]);
      setCandles(candleData);
      setAnalysis(analysisData);
    } catch (error) {
      setCandles([]);
      setAnalysis(null);
      setAnalysisError(error.message);
      if (!isAnalysisPage && !/not currently available for analysis/i.test(error.message)) {
        setNoticeType("error");
        setNotice(error.message);
      }
    } finally {
      setLoading(false);
    }
  }

  async function paperTrade(idea) {
    if (!auth.isAuthenticated || !auth.user) {
      setNoticeType("error");
      setNotice("Sign in to save this trade to History.");
      navigate("/auth");
      return;
    }
    const signalId = signalIdForIdea(idea);
    if (takenSignalIds.has(signalId)) return;
    const freshness = freshnessForIdea(idea);
    const liquidity = liquidityForIdea(idea);
    if (freshness.stale) {
      setNoticeType("error");
      setNotice(`This signal is ${freshness.label.toLowerCase()}. Refresh the market before taking it.`);
      return;
    }
    if (liquidity.blocking) {
      setNoticeType("error");
      setNotice("This signal has low liquidity. SwiftChart blocked it from being saved.");
      return;
    }
    if (!Array.isArray(idea.entry_zone) || idea.entry_zone.length < 2 || !idea.stop_loss || !idea.take_profit_1 || !idea.take_profit_2) {
      setNoticeType("error");
      setNotice("This signal is missing trade data. Refresh and try again.");
      return;
    }
    setPaperTradeLoadingSignalId(signalId);
    setNotice("");
    setNoticeType("info");
    setTakenSignalIds((current) => new Set([...current, signalId]));
    try {
      const savedTrade = await createPaperTradeFromSignal(idea, auth.user.id, auth.session?.access_token);
      setPaperHistoryVersion((value) => value + 1);
      setNoticeType(savedTrade.already_taken ? "info" : "success");
      setNotice(savedTrade.already_taken ? "Trade was already in your history." : "Trade saved to history.");
      if (HYPERLIQUID_REFERRAL_URL && /^https:\/\/(app\.)?hyperliquid\.xyz\//i.test(HYPERLIQUID_REFERRAL_URL)) {
        window.open(HYPERLIQUID_REFERRAL_URL, "_blank", "noopener,noreferrer");
      }
      trackEvent("paper_trade_taken", {
        symbol: idea.symbol,
        timeframe: idea.timeframe,
        direction: idea.direction,
      });
    } catch (error) {
      setTakenSignalIds((current) => {
        const next = new Set(current);
        next.delete(signalId);
        return next;
      });
      setNoticeType("error");
      setNotice(error.message || "Could not save trade to History.");
    } finally {
      setPaperTradeLoadingSignalId("");
    }
  }

  function openPage(nextPage) {
    if (isAnalysisPage) {
      navigate(auth.isAuthenticated ? "/app" : "/preview");
    }
    setPage(nextPage);
    if (nextPage === "dashboard") {
      trackEvent("opened_dashboard");
    }
    if (nextPage === "ideas" || nextPage === "markets") {
      trackEvent("viewed_signal_page", { page: nextPage === "ideas" ? "trade_ideas" : "markets" });
    }
  }

  function openAssetAnalysis(asset) {
    const assetSymbol = String(asset?.symbol || "").replace(/[^a-z0-9]/gi, "").toUpperCase();
    if (!assetSymbol) return;
    setPage("markets");
    setExchange("hyperliquid");
    setSymbol(`${assetSymbol.replace(/(USDT|USDC|USD|PERP)$/i, "")}USDT`);
    setAnalysis(null);
    setCandles([]);
    setAnalysisError("");
    navigate(`/analysis/${assetSymbol.toLowerCase()}`);
    trackEvent("market_intelligence_asset_selected", { symbol: assetSymbol });
  }

  useEffect(() => {
    refreshTopIdeas();
  }, [exchange, timeframe]);

  useEffect(() => {
    if (!auth.user?.id) {
      setTakenSignalIds(new Set());
      return;
    }
    const signalIds = [...topIdeas, ...(analysis?.trade_ideas || [])].map(signalIdForIdea);
    const uniqueSignalIds = [...new Set(signalIds)].filter(Boolean);
    if (!uniqueSignalIds.length) {
      setTakenSignalIds(new Set());
      return;
    }
    let cancelled = false;
    listPaperTradesForSignals(auth.user.id, uniqueSignalIds, auth.session?.access_token)
      .then((rows) => {
        if (!cancelled) {
          setTakenSignalIds(new Set(rows.map((row) => row.signal_id)));
        }
      })
      .catch((error) => {
        if (!cancelled) setNotice(error.message || "Could not check taken trades.");
      });
    return () => {
      cancelled = true;
    };
  }, [auth.user?.id, auth.session?.access_token, topIdeas, analysis, paperHistoryVersion]);

  useEffect(() => {
    const routeSymbol = analysisSymbolFromPath(path);
    if (routeSymbol) {
      setPage("markets");
      setExchange("hyperliquid");
      setSymbol(routeSymbol);
      runAnalysis({ exchange: "hyperliquid", symbol: routeSymbol });
      return;
    }
    if (path === "/" || path === "/preview" || path === "/app") {
      runAnalysis();
    }
  }, [path]);

  useEffect(() => {
    function syncPath() {
      setPath(window.location.pathname);
    }
    window.addEventListener("popstate", syncPath);
    return () => window.removeEventListener("popstate", syncPath);
  }, []);

  useEffect(() => {
    if (auth.loading) return;
    if (isProtectedPage && !auth.isAuthenticated) {
      const returnTo = `${window.location.pathname}${window.location.search}`;
      navigate(`/login?returnTo=${encodeURIComponent(returnTo)}`, { replace: true });
      return;
    }
    if (isCredentialEntryPage && auth.isAuthenticated) {
      const returnTo = new URLSearchParams(window.location.search).get("returnTo");
      navigate(safeReturnTo(returnTo), { replace: true });
      return;
    }
    if (isLaunchPage && auth.isAuthenticated) {
      navigate("/app/home", { replace: true });
      return;
    }
    if (path === "/app" && auth.isAuthenticated) {
      navigate("/app/home", { replace: true });
    }
  }, [auth.loading, auth.isAuthenticated, isProtectedPage, isCredentialEntryPage, isLaunchPage, path]);

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
    ["dashboard", "Crypto"],
    ["forex", "Forex"],
    ["markets", "Markets"],
    ["ideas", "Trade Ideas"],
    ["watchlist", "Watchlist"],
    ["history", "History"],
    ["alerts", "Alerts"],
  ];

  const nav = (
    <nav className={hasWorkspaceChrome ? "nav app-nav" : "nav"} aria-label="SwiftChart sections">
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
          try {
            await auth.signOut();
            navigate("/login", { replace: true });
          } catch {
            // AuthContext exposes a safe, user-facing error in the account menu.
          }
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

  if (isAuthPage && auth.loading) {
    return (
      <>
        <AuthLoading />
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

  if (isProtectedPage && (auth.loading || auth.profileLoading || !auth.isAuthenticated)) {
    return (
      <>
        <AuthLoading />
        <Analytics />
      </>
    );
  }

  if (isMobileDemoPage) {
    return (
      <>
        <main className={`${nightMode ? "app-shell dark-mode" : "app-shell"} mobile-demo-page`}>
          <MobileDemo topIdeas={topIdeas} initialConfigLoading={loadingTopIdeas} />
        </main>
        <Analytics />
      </>
    );
  }

  if (isAdminPaymentsPage) {
    return (
      <>
        {PAYMENTS_ENABLED ? <AdminPayments /> : (
          <main className="payments-admin-page">
            <section className="payments-admin-empty">
              <h1>Payments unavailable</h1>
              <p>{PAYMENTS_COMING_SOON_MESSAGE}</p>
            </section>
          </main>
        )}
        <Analytics />
      </>
    );
  }

  return (
    <>
    <DesktopMobileGate enabled={isAppPage}>
    <main className={`${nightMode ? "app-shell dark-mode" : "app-shell"}${isAppPage ? " app-view" : ""}`}>
      <div className="grain" />
      <div className="cursor-aura" />

      {!hasWorkspaceChrome ? (
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

      {isAppPage ? <MobileDemo topIdeas={topIdeas} initialConfigLoading={loadingTopIdeas} initialTab={appTabFromPath(path)} /> : null}

      <section id="terminal-workspace" className={`terminal-workspace${isAppPage ? " desktop-app-workspace" : ""}`}>
        {!hasWorkspaceChrome ? (
          <div className="workspace-intro">
            <span>SwiftChart</span>
            <h1>Mysterious market structure, made readable.</h1>
            <p>Top trade ideas, range context, chart analysis, alerts, and trade memory stay intact below the quiet terminal shell.</p>
          </div>
        ) : null}

        {hasWorkspaceChrome ? (
          <div className="app-top-controls">
            {nav}
            <div className="app-theme-control">{themeControl}</div>
            {userMenu}
          </div>
        ) : nav}

        {auth.error ? <div className="risk-strip error">{auth.error}</div> : null}
        {notice ? <div className={`risk-strip ${noticeType}`} role="status">{notice}</div> : null}

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
              topIdeasMeta={topIdeasMeta}
              onPaperTrade={paperTrade}
              takenSignalIds={takenSignalIds}
              paperTradeLoadingSignalId={paperTradeLoadingSignalId}
              getSignalId={signalIdForIdea}
              onAnalyzeAsset={openAssetAnalysis}
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
              topIdeasMeta={topIdeasMeta}
              onPaperTrade={paperTrade}
              takenSignalIds={takenSignalIds}
              paperTradeLoadingSignalId={paperTradeLoadingSignalId}
              getSignalId={signalIdForIdea}
              onAnalyzeAsset={openAssetAnalysis}
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
              takenSignalIds={takenSignalIds}
              paperTradeLoadingSignalId={paperTradeLoadingSignalId}
              getSignalId={signalIdForIdea}
              analysisError={analysisError}
            />
          )}
          {page === "forex" && <Forex />}
          {page === "watchlist" && (
            <Watchlist
              pendingSetups={pendingSetups}
              loading={loadingTopIdeas}
              meta={topIdeasMeta}
              exchange={exchange}
              setExchange={setExchange}
              timeframe={timeframe}
              setTimeframe={setTimeframe}
              onRefresh={refreshTopIdeas}
            />
          )}
          {page === "history" && <TradeHistory version={paperHistoryVersion} />}
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
    </DesktopMobileGate>
    <Analytics />
    </>
  );
}

function AuthLoading() {
  return (
    <main className="auth-shell auth-graphite">
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

const rootElement = document.getElementById("root");
const appRoot = window.__swiftChartReactRoot || createRoot(rootElement);
if (import.meta.env.DEV) window.__swiftChartReactRoot = appRoot;

appRoot.render(
  <AuthProvider>
    <App />
  </AuthProvider>
);
