import assert from "node:assert/strict";
import test from "node:test";
import { FOREX_ENABLED, MARKET_OPTIONS, MARKET_TYPES, marketFromSearch, normalizeMarket } from "./markets.js";

test("Forex is paused by default and every market request falls back to crypto", () => {
  assert.equal(FOREX_ENABLED, false);
  assert.deepEqual(MARKET_OPTIONS.map((option) => option.key), [MARKET_TYPES.crypto]);
  assert.equal(normalizeMarket("forex"), MARKET_TYPES.crypto);
  assert.equal(marketFromSearch("?market=forex"), MARKET_TYPES.crypto);
});
