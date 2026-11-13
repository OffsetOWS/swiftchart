import test from "node:test";
import assert from "node:assert/strict";
import { trackedResultR, trackedResultTotal } from "./trackedResults.js";

test("legacy R and incomplete funding cannot appear as net results", () => {
  assert.equal(trackedResultR({ pnl: 4, result_r: 4 }), null);
  assert.equal(trackedResultR({ cost_status: "UNKNOWN", net_r_multiple: 4 }), null);
  assert.equal(trackedResultTotal([{}]).total, null);
});

test("net totals use full precision and expose the excluded count", () => {
  const rows = [.14, .14, -.06].map((net_r_multiple) => ({ cost_status: "MODELED", net_r_multiple }));
  const result = trackedResultTotal([...rows, { pnl: 4 }]);
  assert.ok(Math.abs(result.total - .22) < 1e-12);
  assert.equal(result.sample, 3);
  assert.equal(result.unknown, 1);
  assert.equal(trackedResultR({ cost_status: "MODELED", net_r_multiple: 0 }), 0);
});
