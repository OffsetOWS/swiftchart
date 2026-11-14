import assert from "node:assert/strict";
import test from "node:test";
import { cryptoLifecycleStatus } from "./cryptoLifecycle.js";
import { executionLadderState } from "./executionLadder.js";
test("crypto TP1 means a runner, regardless of realized result sign", () => {
  assert.equal(cryptoLifecycleStatus({status:"TP1_HIT",result:"win"}),"TP1_HIT_TP2_RUNNING");
  assert.equal(executionLadderState({status:cryptoLifecycleStatus({status:"TP1_HIT"})}).label,"TP1 reached · TP2 running");
});
test("a positive time exit never becomes a TP2 claim", () => {
  const status=cryptoLifecycleStatus({lifecycle_status:"TIME_EXIT",result:"win",result_r:1234});
  assert.equal(status,"TIME_EXIT");
  const state=executionLadderState({status});
  assert.match(state.label,/48-hour/);assert.equal(state.tp2Complete,false);assert.equal(state.nearestLevel,null);
});
test("unresolved and ambiguous trades await tracker settlement", () => {
  for (const status of ["UNRESOLVED","AMBIGUOUS","EXPIRED_POSITION","EXPIRED_RUNNER"]) {
    assert.equal(cryptoLifecycleStatus({status}),"UNRESOLVED");
    assert.equal(executionLadderState({status:cryptoLifecycleStatus({status})}).label,"Awaiting tracker settlement");
  }
});

test("unknown market and entry prices remain unknown instead of zero", () => {
  const state=executionLadderState({status:"OPEN",latest_price:null,activated_entry_price:null});
  assert.equal(state.currentPrice,null);assert.equal(state.markerPosition,50);
});
