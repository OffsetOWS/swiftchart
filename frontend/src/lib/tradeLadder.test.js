import assert from "node:assert/strict";
import test from "node:test";
import { buildTradeLadder, formatLadderPrice, formatValidUntil } from "./tradeLadder.js";

const longSignal = {
  direction: "LONG",
  entryLow: 3452,
  entryHigh: 3478,
  rawIdea: { stop_loss: 3395, take_profit_1: 3610, take_profit_2: 3720 },
};

test("long ladder reads top-down by price with the live price inserted in place", () => {
  const ladder = buildTradeLadder(longSignal, { currentPrice: 3531.4 });
  assert.deepEqual(ladder.rows.map((row) => row.key), ["tp2", "tp1", "now", "entry", "stop"]);
  assert.equal(ladder.rows.find((row) => row.key === "stop").r, "−1.0R");
  assert.equal(ladder.rows.find((row) => row.key === "tp1").r, "+2.1R");
  assert.equal(ladder.rows.find((row) => row.key === "entry").display, "3,452 – 3,478");
});

test("short ladder keeps price order and flips R so targets stay positive", () => {
  const ladder = buildTradeLadder({
    direction: "SHORT",
    entryLow: 188.4,
    entryHigh: 190.1,
    rawIdea: { stop_loss: 193.2, take_profit_1: 182, take_profit_2: 177.5 },
  }, { currentPrice: 189.2 });
  assert.deepEqual(ladder.rows.map((row) => row.key), ["stop", "entry", "now", "tp1", "tp2"]);
  assert.ok(ladder.rows.find((row) => row.key === "tp2").r.startsWith("+"));
  assert.ok(ladder.rows.find((row) => row.key === "stop").r.startsWith("−"));
  assert.equal(ladder.rows.find((row) => row.key === "entry").inside, true);
});

test("hit levels drop their distance and prices format by magnitude", () => {
  const ladder = buildTradeLadder(longSignal, { currentPrice: 3650, tp1Complete: true });
  assert.equal(ladder.rows.find((row) => row.key === "tp1").distance, "");
  assert.equal(ladder.rows.find((row) => row.key === "tp1").done, true);
  assert.equal(formatLadderPrice(0.000123456), "0.000123");
  assert.equal(formatLadderPrice(1.23456), "1.2346");
});

test("validity window shows time left", () => {
  const now = Date.parse("2026-11-26T00:00:00Z");
  assert.equal(formatValidUntil("2026-11-27T18:30:00Z", now).left, "42h left");
  assert.equal(formatValidUntil("2026-11-25T18:30:00Z", now).left, "Window ended");
  assert.equal(formatValidUntil(null), null);
});
