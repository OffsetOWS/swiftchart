import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import test from "node:test";

const app = readFileSync(new URL("../App.jsx", import.meta.url), "utf8");
const mobileApp = readFileSync(new URL("../components/MobileDemo.jsx", import.meta.url), "utf8");
const deployScript = readFileSync(new URL("../../../scripts/deploy_frontend.sh", import.meta.url), "utf8");
const vercelConfig = JSON.parse(readFileSync(new URL("../../../vercel.json", import.meta.url), "utf8"));

test("the active application has no legacy desktop view", () => {
  assert.match(app, /import MobileDemo/);
  assert.doesNotMatch(app, /DesktopMobileGate|terminal-workspace|TradeIdeaCard|<Dashboard|<Analysis|<Forex/);
  assert.equal(existsSync(new URL("../components/DesktopMobileGate.jsx", import.meta.url)), false);
  for (const page of ["Dashboard", "Analysis", "Forex", "TradeHistory", "Watchlist"]) {
    assert.equal(existsSync(new URL(`../pages/${page}.jsx`, import.meta.url)), false);
  }
});

test("mobile Take Trade and History flows remain present", () => {
  assert.match(mobileApp, /Trade plan/);
  assert.match(mobileApp, /Saving to History\.\.\./);
  assert.match(mobileApp, /Saved to History/);
  assert.match(mobileApp, /function HistoryScreen/);
  assert.match(mobileApp, /async function saveTradeToHistory/);
  assert.match(mobileApp, /async function takeTrade/);
});

test("SwiftChart Refined preserves the four-item navigation and market branches", () => {
  assert.match(mobileApp, /\["home", "Home", House\]/);
  assert.match(mobileApp, /\["scan", "Scan", ScanLine\]/);
  assert.match(mobileApp, /\["history", "History", History\]/);
  assert.match(mobileApp, /\["account", "Account", UserRound\]/);
  assert.doesNotMatch(mobileApp, /\["trades",\s*"Trades"/i);
  assert.match(mobileApp, /market === MARKET_TYPES\.forex/);
  assert.match(mobileApp, /<ForexHomeScreen/);
  assert.match(mobileApp, /Active signals/);
  assert.match(mobileApp, /aria-label="Open first matching Forex pair"/);
  assert.match(mobileApp, /event\.key === "Enter"/);
});

test("Forex pair search is rendered by Scan, not Home", () => {
  const homeStart = mobileApp.indexOf("function ForexHomeScreen");
  const scanStart = mobileApp.indexOf("function ScanScreen");
  const scanEnd = mobileApp.indexOf("function HistoryScreen", scanStart);
  const homeSource = mobileApp.slice(homeStart, scanStart);
  const scanSource = mobileApp.slice(scanStart, scanEnd);

  assert.doesNotMatch(homeSource, /<ForexPairSearch/);
  assert.match(scanSource, /<ForexPairSearch pairs=\{forexPairs\} onSelectPair=\{onSelectPair\}/);
  assert.equal((mobileApp.match(/aria-label="Search Forex pairs"/g) || []).length, 1);
});

test("refined state presentation reuses one Forex loading state", () => {
  assert.match(mobileApp, /import GraphiteStateCard/);
  assert.equal((mobileApp.match(/title="Loading Forex signals"/g) || []).length, 1);
  assert.match(mobileApp, /tone="cached"/);
  assert.match(mobileApp, /noTrade \? "NO_TRADE"/);
  assert.match(mobileApp, /eyebrow="Authentication required"/);
});

test("crypto home uses the canonical Telegram active-trade feed", () => {
  assert.match(app, /getActiveCryptoTrades/);
  assert.match(app, /activeCryptoTrades=\{activeCryptoTrades\}/);
  assert.match(mobileApp, /Active signals/);
  assert.match(mobileApp, /normalizeActiveCryptoTrade/);
  assert.doesNotMatch(mobileApp, /if \(!signals\.length\) \{[\s\S]*?No confirmed setup right now/);
});

test("the canonical mobile UI remains visible on desktop-width viewports", () => {
  const styles = readFileSync(new URL("../styles/global.css", import.meta.url), "utf8");

  assert.match(
    styles,
    /The graphite application is the canonical UI at every viewport width\. \*\/[\s\n]*@media \(min-width: 0px\) \{[\s\S]*?\.graphite-app \{[\s\S]*?display: flex;/,
  );
});

test("frontend deployments advance and verify the public custom domain", () => {
  assert.match(deployScript, /--prod --yes --json/);
  assert.match(deployScript, /DEPLOYMENT_URL=[\s\S]*?result\.deployment\.url/);
  assert.match(deployScript, /vercel alias set[\s\\\n]*"\$DEPLOYMENT_URL"[\s\\\n]*swiftchart\.xyz/);
  assert.match(deployScript, /PUBLIC_RELEASE=.*swiftchart\.xyz\/release\.json/);
  assert.deepEqual(vercelConfig.headers, [
    {
      source: "/release.json",
      headers: [{ key: "Cache-Control", value: "no-store, max-age=0" }],
    },
  ]);
});
