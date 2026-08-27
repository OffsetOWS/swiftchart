import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const currentDir = path.dirname(fileURLToPath(import.meta.url));
const source = (relativePath) => fs.readFileSync(path.resolve(currentDir, relativePath), "utf8");

test("Forex home places Currency Strength between the session card and signal lists", () => {
  const mobileDemo = source("../components/MobileDemo.jsx");
  const homeStart = mobileDemo.indexOf("function ForexHomeScreen");
  const sessionCard = mobileDemo.indexOf('className="graphite-hero-card forex"', homeStart);
  const strengthCard = mobileDemo.indexOf("<CurrencyStrengthPreview", homeStart);
  const signalLists = mobileDemo.indexOf('className="forex-home-lists"', homeStart);

  assert.ok(homeStart >= 0);
  assert.ok(sessionCard > homeStart);
  assert.ok(strengthCard > sessionCard);
  assert.ok(signalLists > strengthCard);
  assert.match(mobileDemo, /getForexCurrencyStrength\("24H"\)/);
  assert.match(mobileDemo, /OANDA · LIVE · 24H/);
  assert.match(mobileDemo, /Top pair opportunities/);
  assert.match(mobileDemo, /compact \/>/);
});

test("detailed Currency Strength view supports all required horizons", () => {
  const page = source("../pages/CurrencyStrengthPage.jsx");
  const app = source("../App.jsx");

  assert.match(page, /const HORIZONS = \["1H", "4H", "24H"\]/);
  assert.match(page, /useState\("24H"\)/);
  assert.match(page, /setInterval\(\(\) => load\(\{ quiet: true \}\), 60_000\)/);
  assert.match(page, /OANDA · LIVE · informational/);
  assert.match(app, /path === "\/app\/forex\/currency-strength"/);
});

test("missing normalized scores render as unavailable instead of zero", () => {
  const component = source("../components/CurrencyStrength.jsx");

  assert.match(component, /row\.normalized_score != null && Number\.isFinite\(score\)/);
  assert.match(component, /available \? score\.toFixed\(1\) : "—"/);
});

test("strength meters use six progressive red-to-green segments", () => {
  const component = source("../components/CurrencyStrength.jsx");
  const styles = source("../styles/currencyStrength.css");

  assert.match(component, /Array\.from\(\{ length: 6 \}/);
  assert.match(styles, /nth-child\(1\).*#ff2d35/);
  assert.match(styles, /nth-child\(3\).*#ffd500/);
  assert.match(styles, /nth-child\(6\).*#22c55e/);
});
