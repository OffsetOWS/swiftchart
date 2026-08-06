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
  assert.match(mobileApp, /Execution ladder/);
  assert.match(mobileApp, /Saving to History\.\.\./);
  assert.match(mobileApp, /Saved to History/);
  assert.match(mobileApp, /function HistoryScreen/);
  assert.match(mobileApp, /async function saveTradeToHistory/);
  assert.match(mobileApp, /async function takeTrade/);
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
