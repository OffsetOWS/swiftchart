import assert from "node:assert/strict";
import test from "node:test";

import {
  canonicalForexTimeframe,
  filterForexSignalsByTimeframe,
} from "./forexSignals.js";

test("canonicalizes supported Forex timeframe labels", () => {
  assert.equal(canonicalForexTimeframe("1 Hour"), "1H");
  assert.equal(canonicalForexTimeframe("H1"), "1H");
  assert.equal(canonicalForexTimeframe("4 Hours"), "4H");
  assert.equal(canonicalForexTimeframe("h4"), "4H");
  assert.equal(canonicalForexTimeframe("Daily"), "1D");
  assert.equal(canonicalForexTimeframe("D"), "1D");
});

test("strictly filters active and completed signals by canonical timeframe", () => {
  const signals = [
    { id: "active-1h", timeframe: "1H", status: "OPEN" },
    { id: "active-4h", timeframe: "H4", status: "OPEN" },
    { id: "completed-4h", timeframe: "4 Hours", status: "TP2_HIT" },
    { id: "completed-1d", timeframe: "Daily", status: "STOPPED" },
  ];

  assert.deepEqual(
    filterForexSignalsByTimeframe(signals, "4H").map((signal) => signal.id),
    ["active-4h", "completed-4h"],
  );
});
