import assert from "node:assert/strict";
import test from "node:test";
import { formatChangePct, summariseBtcCandles } from "./btcMarket.js";

function candles(closes) {
  const start = Date.parse("2026-11-20T00:00:00Z");
  return closes.map((close, index) => ({
    timestamp: new Date(start + index * 3_600_000).toISOString(),
    open: close,
    high: close + 100,
    low: close - 100,
    close,
  }));
}

test("summarises price, 24h change and the recent range", () => {
  const closes = Array.from({ length: 80 }, (_, index) => 95_000 + index * 10);
  const summary = summariseBtcCandles(candles(closes));
  assert.equal(summary.price, 95_790);
  assert.equal(summary.priceLabel, "95,790");
  assert.ok(summary.changePct > 0);
  assert.equal(summary.rangeHighLabel, "95.9k");
  assert.ok(summary.rangePosition > 85 && summary.rangePosition < 95);
});

test("handles missing data", () => {
  assert.equal(summariseBtcCandles([]), null);
  assert.equal(formatChangePct(1.234), "+1.2%");
  assert.equal(formatChangePct(-0.5), "−0.5%");
  assert.equal(formatChangePct(null), "");
});
