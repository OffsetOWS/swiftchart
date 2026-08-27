import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const mobileApp = readFileSync(new URL("../components/MobileDemo.jsx", import.meta.url), "utf8");

test("crypto history refreshes lifecycle and live price for signed-in users", () => {
  assert.match(mobileApp, /window\.setInterval\(loadPaperTrades, 30_000\)/);
  assert.match(mobileApp, /selectedSignal\.isHistory[\s\S]*?getPaperTradeDetail/);
  assert.match(mobileApp, /getCandles\(\{[\s\S]*?selectedSignal\.symbol/);
});

test("saved crypto detail feeds lifecycle and latest price into the execution ladder", () => {
  assert.match(mobileApp, /status: lifecycleStatus/);
  assert.match(mobileApp, /latestPrice: trade\.latest_price/);
  assert.match(mobileApp, /Nearest:[\s\S]*?nearestDistancePct/);
});
