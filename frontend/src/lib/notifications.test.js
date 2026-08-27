import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { notificationStorageKey } from "./notifications.js";

const source = readFileSync(new URL("./notifications.js", import.meta.url), "utf8");
const mobileApp = readFileSync(new URL("../components/MobileDemo.jsx", import.meta.url), "utf8");

test("notification storage is isolated by signed-in user", () => {
  assert.equal(notificationStorageKey("user-a"), "swiftchart.notifications.v2:user-a");
  assert.equal(notificationStorageKey("user-b"), "swiftchart.notifications.v2:user-b");
  assert.notEqual(notificationStorageKey("user-a"), notificationStorageKey("user-b"));
});

test("notification center does not seed fabricated market activity", () => {
  assert.doesNotMatch(source, /defaultNotifications|high-conviction-btc|near-entry-eth|stop-loss-avax|tp1-sol/);
  assert.match(source, /return \[\];/);
});

test("taking a trade creates an account-specific activity notification", () => {
  assert.match(mobileApp, /useNotifications\(auth\.user\?\.id\)/);
  assert.match(mobileApp, /pushSwiftChartNotification\(\{/);
  assert.match(mobileApp, /statusBadge: "Trade Opened"/);
});
