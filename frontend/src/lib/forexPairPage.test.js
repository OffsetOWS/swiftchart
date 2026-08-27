import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const page = readFileSync(new URL("../pages/ForexPairPage.jsx", import.meta.url), "utf8");
const chart = readFileSync(new URL("../components/ForexPairChart.jsx", import.meta.url), "utf8");
const styles = readFileSync(new URL("../styles/forexPair.css", import.meta.url), "utf8");

test("Forex pair page keeps zones fixed and removes redundant chart controls", () => {
  assert.doesNotMatch(page, /forex-live-pill|>Structure<|>Clean<|forex-chart-layers/);
  assert.doesNotMatch(page, /setLayer|useState\("zones"\)/);
  assert.match(chart, /BUY" : "SELL"\} ZONE/);
});

test("Swift TA uses the compact two-column level layout", () => {
  assert.match(styles, /\.swift-ta-card dl \{[^}]*grid-template-columns: repeat\(2, minmax\(0, 1fr\)\)/);
  assert.match(styles, /\.swift-ta-card dl > div:first-child \{ grid-column: 1 \/ -1; \}/);
});
