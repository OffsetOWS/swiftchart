import assert from "node:assert/strict";
import test from "node:test";
import { formatR, normalizeOutcomes, regimeFromScanStats, timeAgo } from "./homeSummary.js";

test("regime maps the global score into three plain labels", () => {
  assert.equal(regimeFromScanStats({ global_regime_score: 40 }).label, "Trending up");
  assert.equal(regimeFromScanStats({ global_regime_score: -30 }).label, "Trending down");
  assert.equal(regimeFromScanStats({ global_regime_score: 5 }).label, "Range-bound");
  assert.equal(regimeFromScanStats({}).key, "unknown");
  assert.equal(regimeFromScanStats({ global_regime_score: 0 }).position, 50);
});

test("outcomes keep only closed trades, newest first, with R values", () => {
  const outcomes = normalizeOutcomes([
    { id: 1, symbol: "ETHUSDT", direction: "LONG", result: "WIN", tp1_hit_at: "2026-11-25T10:00:00Z", tp2_hit_at: "2026-11-25T12:00:00Z", closed_at: "2026-11-25T12:00:00Z", pnl_r_multiple: 3.4 },
    { id: 2, symbol: "SOLUSDT", direction: "SHORT", result: "LOSS", sl_hit_at: "2026-11-25T14:00:00Z", closed_at: "2026-11-25T14:00:00Z", pnl_r_multiple: -1 },
    { id: 3, symbol: "BTCUSDT", direction: "LONG", result: "OPEN" },
    { id: 4, symbol: "LINKUSDT", direction: "LONG", result: "NO_ENTRY" },
  ]);
  assert.deepEqual(outcomes.map((item) => item.id), [2, 1]);
  assert.equal(outcomes[0].event, "Stopped");
  assert.equal(outcomes[0].direction, "short");
  assert.equal(outcomes[1].event, "TP2 hit");
  assert.equal(outcomes[1].symbol, "ETH");
});

test("R values and recency format cleanly", () => {
  assert.equal(formatR(2.14), "+2.1R");
  assert.equal(formatR(-1), "−1.0R");
  assert.equal(formatR(0.01), "0.0R");
  assert.equal(formatR(null), "—");
  const now = Date.parse("2026-11-26T12:00:00Z");
  assert.equal(timeAgo("2026-11-26T11:45:00Z", now), "15m ago");
  assert.equal(timeAgo("2026-11-26T09:00:00Z", now), "3h ago");
  assert.equal(timeAgo("2026-11-24T12:00:00Z", now), "2d ago");
});
