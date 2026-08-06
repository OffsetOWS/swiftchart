import test from "node:test";
import assert from "node:assert/strict";

import { executionLadderState } from "./executionLadder.js";

const long = {
  direction: "LONG",
  status: "OPEN",
  entry_low: 100,
  entry_high: 101,
  stop_loss: 95,
  take_profit_1: 110,
  take_profit_2: 120,
  activated_entry_price: 100.5,
};

const short = {
  direction: "SHORT",
  status: "OPEN",
  entry_low: 100,
  entry_high: 101,
  stop_loss: 106,
  take_profit_1: 92,
  take_profit_2: 85,
  activated_entry_price: 100.5,
};

test("LONG marker moves favorably toward TP1 and unfavorably toward stop", () => {
  const profit = executionLadderState({ ...long, latest_price: 108 });
  const loss = executionLadderState({ ...long, latest_price: 97 });
  assert.equal(profit.label, "In profit");
  assert.equal(loss.label, "In loss");
  assert.ok(profit.markerPosition > loss.markerPosition);
  assert.ok(profit.favorableProgress > 0);
  assert.ok(loss.favorableProgress < 0);
});

test("SHORT marker moves favorably down toward TP1 and unfavorably up toward stop", () => {
  const profit = executionLadderState({ ...short, latest_price: 94 });
  const loss = executionLadderState({ ...short, latest_price: 104 });
  assert.equal(profit.label, "In profit");
  assert.equal(loss.label, "In loss");
  assert.ok(profit.markerPosition < loss.markerPosition);
  assert.ok(profit.favorableProgress > 0);
  assert.ok(loss.favorableProgress < 0);
});

test("terminal and target states complete the correct ladder points", () => {
  const tp1 = executionLadderState({ ...long, status: "TP1_HIT", latest_price: 112 });
  const partial = executionLadderState({ ...long, status: "TP1_HIT_TP2_RUNNING", latest_price: 112 });
  const tp2 = executionLadderState({ ...long, status: "TP2_HIT", latest_price: 125 });
  const stopped = executionLadderState({ ...short, status: "STOPPED", latest_price: 110 });
  assert.equal(tp1.tp1Complete, true);
  assert.equal(tp1.tp2Complete, false);
  assert.equal(tp1.label, "TP1 reached · position closed");
  assert.equal(partial.tp1Complete, true);
  assert.equal(partial.label, "TP1 reached · TP2 running");
  assert.equal(tp2.markerPosition, 100);
  assert.equal(tp2.tp1Complete, true);
  assert.equal(tp2.tp2Complete, true);
  assert.equal(stopped.markerPosition, 100);
  assert.equal(stopped.stopComplete, true);
});

test("marker is clamped beyond target and stop", () => {
  assert.equal(executionLadderState({ ...long, latest_price: 999 }).markerPosition, 100);
  assert.equal(executionLadderState({ ...long, latest_price: 1 }).markerPosition, 0);
  assert.equal(executionLadderState({ ...short, latest_price: 1 }).markerPosition, 0);
  assert.equal(executionLadderState({ ...short, latest_price: 999 }).markerPosition, 100);
});
