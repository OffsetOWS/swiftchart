import assert from "node:assert/strict";
import test from "node:test";
import { buildHistoryRow, historyStatus } from "./historyRows.js";

test("labels come from the lifecycle status, not the backend's title-cased label", () => {
  assert.equal(historyStatus({ lifecycle_status: "SL_HIT", lifecycle_label: "Sl Hit" }).label, "Stopped");
  assert.equal(historyStatus({ lifecycle_status: "TP2_HIT", lifecycle_label: "Tp2 Hit" }).label, "TP2 hit");
  assert.equal(historyStatus({ status: "taken", lifecycle_label: "Costs unknown" }).label, "Open");
  assert.equal(historyStatus({ status: "closed", result: "win" }).group, "tp2");
});

test("rows show R before costs and no R while open", () => {
  const closed = buildHistoryRow({ id: 1, symbol: "SOLUSDT", direction: "SHORT", lifecycle_status: "TP2_HIT", gross_r_multiple: 3, result_r: 2.8, taken_at: "2026-11-25T10:00:00Z" });
  assert.equal(closed.r, 3);
  assert.equal(closed.base, "SOL");
  assert.equal(closed.direction, "short");
  const open = buildHistoryRow({ id: 2, symbol: "ETHUSDT", status: "taken", result_r: 0.4 });
  assert.equal(open.r, null);
  assert.equal(open.group, "open");
});
