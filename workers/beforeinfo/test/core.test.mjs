import { test } from "node:test";
import assert from "node:assert/strict";
import {
  shouldFetch, parseWindow, officialUrl, parseClosedAt, parseDailyLimit, resultKey, shouldDispatch, waitBeforeNext,
} from "../src/core.mjs";

test("窓の判定(1レース1回だけ)", () => {
  for (const m of [15, 12.5, 10]) assert.equal(shouldFetch(m, undefined), true, String(m));
  for (const m of [15.1, 30, 4.9, 0, -3]) assert.equal(shouldFetch(m, undefined), false, String(m));
  assert.equal(shouldFetch(7, undefined), true); // 3ページ上限で回されたレースは5分前まで
  for (const s of ["ok", "partial", "not_posted", "failed"]) assert.equal(shouldFetch(12, { attempt: 1, status: s }), false, s);
});

test("URL・キー・上限・間隔・dispatch", () => {
  assert.equal(officialUrl(2, 1, "20261106"), "https://www.boatrace.jp/owpc/pc/race/beforeinfo?rno=1&jcd=02&hd=20261106");
  assert.equal(resultKey("20261106", 2, 1), "beforeinfo/20261106/02-01.json");
  assert.equal(parseClosedAt("2026-11-06T10:45:00+09:00"), Date.parse("2026-11-06T01:45:00Z"));
  assert.equal(parseClosedAt("2026-11-06 10:45:00"), Date.parse("2026-11-06T01:45:00Z"));
  assert.equal(parseDailyLimit(undefined), 300);
  assert.equal(parseDailyLimit("200"), 200);
  assert.equal(waitBeforeNext(null, 0), 0);
  assert.equal(waitBeforeNext(1000, 1500), 1000);
  assert.equal(shouldDispatch([], null, 0), false);
  assert.equal(shouldDispatch(["a"], null, 0), true);
  assert.equal(shouldDispatch(["a"], 0, 239_999), false);
  assert.equal(shouldDispatch(["a"], 0, 240_000), true);
});

test("取得の時間帯は環境変数で変えられる(不正なら既定)", () => {
  assert.deepEqual(parseWindow({}), { start: 15, end: 10, last: 5 });
  assert.deepEqual(parseWindow({ WINDOW_START_MIN: "20", WINDOW_END_MIN: "14", LAST_CHANCE_MIN: "8" }), { start: 20, end: 14, last: 8 });
  assert.deepEqual(parseWindow({ WINDOW_START_MIN: "8", WINDOW_END_MIN: "12" }), { start: 15, end: 10, last: 5 }); // 順序が逆
  assert.deepEqual(parseWindow({ LAST_CHANCE_MIN: "2" }), { start: 15, end: 10, last: 5 }); // 4分前以降は不可
  const w = parseWindow({ WINDOW_START_MIN: "20", WINDOW_END_MIN: "15", LAST_CHANCE_MIN: "10" });
  assert.equal(shouldFetch(18, undefined, w), true);
  assert.equal(shouldFetch(9, undefined, w), false);
});
