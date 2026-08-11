import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const mobileApp = readFileSync(new URL("../components/MobileDemo.jsx", import.meta.url), "utf8");
const stateCard = readFileSync(new URL("../components/GraphiteStateCard.jsx", import.meta.url), "utf8");
const styles = readFileSync(new URL("../styles/global.css", import.meta.url), "utf8");
const refinedStyles = styles.slice(styles.indexOf("/* SwiftChart Refined: preservation-first presentation layer. */"));

test("GraphiteStateCard remains a presentation-only component", () => {
  assert.match(stateCard, /function GraphiteStateCard|export default function GraphiteStateCard/);
  assert.match(stateCard, /aria-live/);
  assert.match(stateCard, /aria-busy/);
  assert.doesNotMatch(stateCard, /fetch\(|localStorage|sessionStorage|window\.location|history\./);
});

test("production behavior sources remain connected", () => {
  assert.match(mobileApp, /executionLadderState\(signal\)/);
  assert.match(mobileApp, /onCryptoScan/);
  assert.match(mobileApp, /onForexScan/);
  assert.match(mobileApp, /saveTradeToHistory/);
  assert.match(mobileApp, /setActiveMarket/);
  assert.match(mobileApp, /MarketToggle/);
});

test("Refined motion is scoped and reduced-motion aware", () => {
  assert.match(refinedStyles, /--ease-out:\s*cubic-bezier\(0\.23,\s*1,\s*0\.32,\s*1\)/);
  assert.match(refinedStyles, /@media \(prefers-reduced-motion:\s*reduce\)/);
  assert.match(styles, /\.graphite-app\.reduce-animations/);
  assert.doesNotMatch(refinedStyles, /transition:\s*all(?:\s|;)/);
  assert.doesNotMatch(refinedStyles, /transform:\s*scale\(0\)/);
  assert.doesNotMatch(refinedStyles, /\bease-in\b(?!-out)/);
});

test("navigation remains four items and Active Trades remains in Home", () => {
  const navItemsBlock = mobileApp.match(/const navItems = \[([\s\S]*?)\];/)?.[1] || "";
  assert.equal((navItemsBlock.match(/^\s*\[/gm) || []).length, 4);
  assert.doesNotMatch(navItemsBlock, /trades/i);
  assert.match(mobileApp, /activeSignals\.slice/);
  assert.doesNotMatch(mobileApp, /\/app\/trades/);
});
