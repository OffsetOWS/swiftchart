import { lazy, Suspense, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { Analytics } from "@vercel/analytics/react";
import MobileDemo from "./components/MobileDemo.jsx";
const Auth = lazy(() => import("./pages/Auth.jsx"));
const Docs = lazy(() => import("./pages/Docs.jsx"));
const LaunchFlow = lazy(() => import("./pages/LaunchFlow.jsx"));
const PairChartScreen = lazy(() => import("./pages/ForexPairPage.jsx"));
const CurrencyStrengthPage = lazy(() => import("./pages/CurrencyStrengthPage.jsx"));
import { AuthProvider, useAuth } from "./lib/AuthContext.jsx";
import { getCanonicalRedirectUrl } from "./lib/siteUrl.js";
import { FOREX_ENABLED } from "./lib/markets.js";
import { getActiveCryptoTrades, getCandles, getTopIdeas, getTradeHistory, refreshTopIdeasCache } from "./lib/api.js";
import { summariseBtcCandles } from "./lib/btcMarket.js";
import { normalizeOutcomes } from "./lib/homeSummary.js";
import "./styles/global.css";
import "./styles/terminal.css";

function appTabFromPath(pathname) {
  const match = String(pathname || "").match(/^\/app\/(home|scan|history|account|notifications)$/i);
  if (match) return match[1].toLowerCase();
  return /^\/app\/signal\/[^/]+$/i.test(String(pathname || "")) ? "scan" : "home";
}

function safeReturnTo(value, fallback = "/app/home") {
  return value?.startsWith("/") && !value.startsWith("//") ? value : fallback;
}

export default function App() {
  const auth = useAuth();
  const [path, setPath] = useState(window.location.pathname);
  const [topIdeas, setTopIdeas] = useState([]);
  const [pendingSetups, setPendingSetups] = useState([]);
  const [activeCryptoTrades, setActiveCryptoTrades] = useState([]);
  const [loadingTopIdeas, setLoadingTopIdeas] = useState(false);
  const [scanMeta, setScanMeta] = useState(null);
  const [recentOutcomes, setRecentOutcomes] = useState([]);
  const [btcMarket, setBtcMarket] = useState(null);

  const isLandingPage = path === "/";
  const isLaunchPage = path === "/launch";
  const isPreviewPage = path === "/preview";
  const isMobileAlias = path === "/mobile-demo";
  const isAppPage = path === "/app"
    || /^\/app\/(home|scan|history|account|notifications)$/.test(path)
    || (FOREX_ENABLED && /^\/app\/signal\/[^/]+$/.test(path))
    || (FOREX_ENABLED && path === "/app/forex/currency-strength")
    || (FOREX_ENABLED && /^\/app\/forex\/[A-Z0-9]+$/i.test(path));
  const isPublicReadOnlyAppPage = /^\/app\/(?:home|scan)(?:\/|$)/i.test(path)
    || (FOREX_ENABLED && /^\/app\/signal\/[^/]+$/i.test(path))
    || (FOREX_ENABLED && path === "/app/forex/currency-strength")
    || (FOREX_ENABLED && /^\/app\/forex\/[A-Z0-9]+$/i.test(path));
  const isDisabledCommercePage = /^\/(?:admin\/payments?|payments?|pricing|billing|subscribe)(?:\/|$)/i.test(path)
    || /^\/app\/(?:upgrade|payments?|pricing|billing|subscribe)(?:\/|$)/i.test(path);
  const isAuthPage = ["/auth", "/login", "/signup", "/forgot-password", "/reset-password"].includes(path);
  const isCredentialEntryPage = ["/auth", "/login", "/signup"].includes(path);
  const isDocsPage = path === "/docs" || path.startsWith("/docs/");
  const isProtectedPage = (isAppPage && !isPublicReadOnlyAppPage) || isMobileAlias;

  function navigate(nextPath, { replace = false } = {}) {
    const nextUrl = new URL(nextPath, window.location.origin);
    if (`${window.location.pathname}${window.location.search}` !== `${nextUrl.pathname}${nextUrl.search}`) {
      window.history[replace ? "replaceState" : "pushState"]({}, "", `${nextUrl.pathname}${nextUrl.search}`);
    }
    setPath(nextUrl.pathname);
  }

  async function refreshTopIdeas({ manual = false, silent = false } = {}) {
    if (!silent && topIdeas.length === 0 && pendingSetups.length === 0) setLoadingTopIdeas(true);
    try {
      if (manual) await refreshTopIdeasCache({ exchange: "all", timeframe: "4h" });
      const [ideasResult, activeResult, historyResult, btcResult] = await Promise.allSettled([
        getTopIdeas({ exchange: "all", timeframe: "4h" }),
        getActiveCryptoTrades("all"),
        getTradeHistory({ limit: 40, sort: "desc" }),
        getCandles({ exchange: "hyperliquid", symbol: "BTCUSDT", timeframe: "1h" }),
      ]);
      if (btcResult.status === "fulfilled") {
        const summary = summariseBtcCandles(btcResult.value);
        if (summary) setBtcMarket(summary);
      }
      if (ideasResult.status === "fulfilled") {
        const value = ideasResult.value || {};
        setTopIdeas(value.ideas || []);
        setPendingSetups(value.pending_setups || []);
        setScanMeta({
          scanStats: value.scan_stats || null,
          lastScanAt: value.last_refresh_finished_at || null,
          refreshing: Boolean(value.refresh_in_progress),
        });
      }
      if (historyResult.status === "fulfilled") {
        setRecentOutcomes(normalizeOutcomes(historyResult.value?.records || []));
      }
      if (activeResult.status === "fulfilled") {
        setActiveCryptoTrades(activeResult.value.trades || []);
      }
      return ideasResult.status === "fulfilled" || activeResult.status === "fulfilled";
    } catch {
      return false;
    } finally {
      setLoadingTopIdeas(false);
    }
  }

  useEffect(() => {
    refreshTopIdeas();
    const timer = window.setInterval(() => refreshTopIdeas({ silent: true }), 60_000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    const syncPath = () => setPath(window.location.pathname);
    window.addEventListener("popstate", syncPath);
    return () => window.removeEventListener("popstate", syncPath);
  }, []);

  useEffect(() => {
    if (auth.loading) return;
    if (isLandingPage && auth.isAuthenticated) {
      navigate("/app/home", { replace: true });
      return;
    }
    if (isDisabledCommercePage || path.startsWith("/analysis/")) {
      navigate("/app/scan", { replace: true });
      return;
    }
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
    if (path === "/app") {
      navigate("/app/home", { replace: true });
      return;
    }
    const knownRoute = isLandingPage || isLaunchPage || isDocsPage || isAuthPage
      || isPreviewPage || isMobileAlias || isAppPage;
    if (!knownRoute) navigate("/app/home", { replace: true });
  }, [
    auth.loading,
    auth.isAuthenticated,
    isAppPage,
    isAuthPage,
    isCredentialEntryPage,
    isDisabledCommercePage,
    isDocsPage,
    isLandingPage,
    isLaunchPage,
    isMobileAlias,
    isPreviewPage,
    isProtectedPage,
    path,
  ]);

  if (isLandingPage) return auth.loading || auth.isAuthenticated ? <AuthLoading /> : <><LaunchFlow /><Analytics /></>;
  if (isLaunchPage) return <><LaunchFlow /><Analytics /></>;
  if (isDocsPage) return <><Docs /><Analytics /></>;
  if (isAuthPage && auth.loading) return <><AuthLoading /><Analytics /></>;
  if (isAuthPage) return <><Auth /><Analytics /></>;
  if (isProtectedPage && (auth.loading || auth.profileLoading || !auth.isAuthenticated)) {
    return <><AuthLoading /><Analytics /></>;
  }
  if (isDisabledCommercePage || path.startsWith("/analysis/")) return <AuthLoading />;

  if (!isAppPage && path.startsWith("/app/")) return <AuthLoading />;

  if (path === "/app/forex/currency-strength") {
    return (
      <>
        <main className="app-shell dark-mode app-view mobile-only-app">
          <CurrencyStrengthPage onNavigate={navigate} />
        </main>
        <Analytics />
      </>
    );
  }

  const forexPairMatch = path.match(/^\/app\/forex\/([A-Z0-9]+)$/i);
  if (forexPairMatch) {
    return (
      <>
        <main className="app-shell dark-mode app-view mobile-only-app">
          <PairChartScreen symbol={forexPairMatch[1].toUpperCase()} onNavigate={navigate} />
        </main>
        <Analytics />
      </>
    );
  }

  return (
    <>
      <main className="app-shell dark-mode app-view mobile-only-app">
        <MobileDemo
          topIdeas={topIdeas}
          pendingSetups={pendingSetups}
          activeCryptoTrades={activeCryptoTrades}
          initialConfigLoading={loadingTopIdeas}
          initialTab={appTabFromPath(path)}
          scanMeta={scanMeta}
          btcMarket={btcMarket}
          recentOutcomes={recentOutcomes}
          onRefreshCrypto={() => refreshTopIdeas({ manual: true })}
          onNavigate={navigate}
        />
      </main>
      <Analytics />
    </>
  );
}

function AuthLoading() {
  return (
    <main className="auth-shell auth-graphite">
      <section className="auth-card auth-loading-card" aria-live="polite">
        <span className="auth-wordmark">swiftchart</span>
        <div className="auth-copy">
          <p>Signing you in…</p>
        </div>
        <div className="launch-progress"><span /></div>
      </section>
    </main>
  );
}

const rootElement = document.getElementById("root");
const appRoot = window.__swiftChartReactRoot || createRoot(rootElement);
if (import.meta.env.DEV) window.__swiftChartReactRoot = appRoot;

const canonicalRedirectUrl = getCanonicalRedirectUrl();
if (canonicalRedirectUrl) {
  window.location.replace(canonicalRedirectUrl);
} else {
  appRoot.render(<AuthProvider><Suspense fallback={<div className="app-suspense" />}><App /></Suspense></AuthProvider>);
}
