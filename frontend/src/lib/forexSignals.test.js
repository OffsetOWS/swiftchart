import assert from "node:assert/strict";
import test from "node:test";

import {
  activeForexSignals,
  canonicalForexTimeframe,
  filterForexSignalsByTimeframe,
} from "./forexSignals.js";

test("canonicalizes supported Forex timeframe labels", () => {
  assert.equal(canonicalForexTimeframe("15 Minutes"), "15M");
  assert.equal(canonicalForexTimeframe("M15"), "15M");
  assert.equal(canonicalForexTimeframe("1 Hour"), "1H");
  assert.equal(canonicalForexTimeframe("H1"), "1H");
  assert.equal(canonicalForexTimeframe("4 Hours"), "4H");
  assert.equal(canonicalForexTimeframe("h4"), "4H");
  assert.equal(canonicalForexTimeframe("Daily"), "1D");
  assert.equal(canonicalForexTimeframe("D"), "1D");
});

test("keeps only live and pending Forex opportunities in the app feed", () => {
  const signals = [
    { id: "pending", status: "PENDING_ENTRY" },
    { id: "open", status: "OPEN" },
    { id: "partial", status: "TP1_HIT" },
    { id: "won", status: "TP2_HIT" },
    { id: "lost", status: "STOPPED" },
    { id: "expired", status: "EXPIRED" },
  ];

  assert.deepEqual(
    activeForexSignals(signals).map((signal) => signal.id),
    ["pending", "open"],
  );
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
