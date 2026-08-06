import { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { Analytics } from "@vercel/analytics/react";
import MobileDemo from "./components/MobileDemo.jsx";
import Auth from "./pages/Auth.jsx";
import Docs from "./pages/Docs.jsx";
import Landing from "./pages/Landing.jsx";
import LaunchFlow from "./pages/LaunchFlow.jsx";
import { AuthProvider, useAuth } from "./lib/AuthContext.jsx";
import { getCanonicalRedirectUrl } from "./lib/siteUrl.js";
import { getTopIdeas, refreshTopIdeasCache } from "./lib/api.js";
import swiftChartLogo from "./assets/swiftchart-logo.png";
import "./styles/global.css";

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
  const [loadingTopIdeas, setLoadingTopIdeas] = useState(false);

  const isLandingPage = path === "/";
  const isLaunchPage = path === "/launch";
  const isPreviewPage = path === "/preview";
  const isMobileAlias = path === "/mobile-demo";
  const isAppPage = path === "/app"
    || /^\/app\/(home|scan|history|account|notifications)$/.test(path)
    || /^\/app\/signal\/[^/]+$/.test(path);
  const isPublicReadOnlyAppPage = /^\/app\/(?:home|scan)(?:\/|$)/i.test(path)
    || /^\/app\/signal\/[^/]+$/i.test(path);
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
      const data = await getTopIdeas({ exchange: "all", timeframe: "4h" });
      setTopIdeas(data.ideas || []);
      setPendingSetups(data.pending_setups || []);
      return true;
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

  if (isLandingPage && (auth.loading || auth.isAuthenticated)) return <AuthLoading />;
  if (isLandingPage) return <><Landing /><Analytics /></>;
  if (isLaunchPage) return <><LaunchFlow /><Analytics /></>;
  if (isDocsPage) return <><Docs /><Analytics /></>;
  if (isAuthPage && auth.loading) return <><AuthLoading /><Analytics /></>;
  if (isAuthPage) return <><Auth /><Analytics /></>;
  if (isProtectedPage && (auth.loading || auth.profileLoading || !auth.isAuthenticated)) {
    return <><AuthLoading /><Analytics /></>;
  }
  if (isDisabledCommercePage || path.startsWith("/analysis/")) return <AuthLoading />;

  return (
    <>
      <main className="app-shell dark-mode app-view mobile-only-app">
        <MobileDemo
          topIdeas={topIdeas}
          pendingSetups={pendingSetups}
          initialConfigLoading={loadingTopIdeas}
          initialTab={appTabFromPath(path)}
          onRefreshCrypto={() => refreshTopIdeas({ manual: true })}
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
        <div className="launch-mark" aria-hidden="true"><img src={swiftChartLogo} alt="" /></div>
        <div className="auth-copy">
          <span className="eyebrow">SwiftChart account</span>
          <h1>Restoring session</h1>
          <p>Loading your dashboard, profile, and saved SwiftChart access.</p>
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
  appRoot.render(<AuthProvider><App /></AuthProvider>);
}
