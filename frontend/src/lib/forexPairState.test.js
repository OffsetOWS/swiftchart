import assert from "node:assert/strict";
import test from "node:test";

import { forexZoneStatus } from "./forexPairState.js";

function snapshot(decision, state) {
  return {
    bias: "BULLISH",
    decision,
    buy_zones: [{ state }],
    sell_zones: [],
  };
}

test("a confirmed zone does not manufacture a directional UI entry", () => {
  assert.equal(forexZoneStatus(snapshot("NO_TRADE", "CONFIRMED")), "Entry requirements not met");
  assert.equal(forexZoneStatus(snapshot("WAIT", "RECLAIMED")), "Reaction detected");
});

test("directional confirmation is shown only for an approved entry", () => {
  assert.equal(forexZoneStatus(snapshot("LONG", "CONFIRMED")), "LONG confirmed");
});
