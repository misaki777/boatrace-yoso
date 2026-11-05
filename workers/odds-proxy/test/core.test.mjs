// 純粋な関数のテスト(日付・締切・入力チェック・キャッシュキー・上限・認証・CORS)。

import { test } from "node:test";
import assert from "node:assert/strict";

import {
  CACHE_TTL_SEC,
  DEFAULT_DAILY_LIMIT,
  MAX_DAILY_LIMIT,
  cacheKey,
  countKey,
  formatJstIso,
  isCacheFresh,
  isClosed,
  isOriginAllowed,
  jstDateString,
  officialUrl,
  parseAllowedOrigins,
  parseClosedAt,
  parseCount,
  parseDailyLimit,
  stoppedKey,
  timingSafeEqual,
  validateQuery,
  waitBeforeNext,
  withinDailyLimit,
} from "../src/core.mjs";

const NOW = Date.parse("2026-11-03T10:30:00+09:00");
const q = (obj) => new URLSearchParams(obj);
const OK_QUERY = { date: "20261103", jcd: "02", rno: "1", closed_at: "2026-11-03T10:45:00+09:00" };

test("JST の日付と時刻", () => {
  assert.equal(jstDateString(Date.parse("2026-11-03T14:59:59Z")), "20261103");
  assert.equal(jstDateString(Date.parse("2026-11-03T15:00:00Z")), "20261104"); // JST の0時
  assert.equal(formatJstIso(Date.parse("2026-11-03T01:40:12.789Z")), "2026-11-03T10:40:12+09:00");
});

test("parseClosedAt: 時差あり・なし・Z・不正", () => {
  assert.equal(parseClosedAt("2026-11-03T10:45:00+09:00"), Date.parse("2026-11-03T01:45:00Z"));
  assert.equal(parseClosedAt("2026-11-03 10:45:00"), Date.parse("2026-11-03T01:45:00Z")); // JST とみなす
  assert.equal(parseClosedAt("2026-11-03T01:45:00Z"), Date.parse("2026-11-03T01:45:00Z"));
  assert.equal(parseClosedAt("2026-11-03T10:45+09:00"), Date.parse("2026-11-03T01:45:00Z"));
  assert.equal(parseClosedAt("2026-02-30T10:45:00+09:00"), null);
  assert.equal(parseClosedAt("10:45"), null);
  assert.equal(parseClosedAt(null), null);
});

test("validateQuery: 正しい要求", () => {
  const r = validateQuery(q(OK_QUERY), NOW);
  assert.equal(r.ok, true);
  assert.deepEqual(r.value.types, ["3t", "2tf", "tf"]);
  assert.equal(r.value.raceId, "20261103-02-01");
  assert.equal(r.value.rno, 1);
  const r2 = validateQuery(q({ ...OK_QUERY, rno: "12", types: "tf,3t,tf" }), NOW);
  assert.deepEqual(r2.value.types, ["3t", "tf"]); // 重複を除き、取得の順番にそろえる
  assert.equal(r2.value.raceId, "20261103-02-12");
});

test("validateQuery: 誤りは ok=false", () => {
  const bad = [
    { ...OK_QUERY, date: "20261102" }, // 過去日
    { ...OK_QUERY, date: "20261104" }, // 未来日
    { ...OK_QUERY, date: "2026-11-03" },
    { ...OK_QUERY, jcd: "2" },
    { ...OK_QUERY, jcd: "25" },
    { ...OK_QUERY, jcd: "00" },
    { ...OK_QUERY, rno: "0" },
    { ...OK_QUERY, rno: "13" },
    { ...OK_QUERY, rno: "1a" },
    { ...OK_QUERY, types: "3f" }, // 3連複は扱わない
    { ...OK_QUERY, types: "3t,k" },
    { ...OK_QUERY, types: "3t," },
    { ...OK_QUERY, closed_at: "" },
    { ...OK_QUERY, closed_at: "2026-11-04T10:45:00+09:00" }, // date と違う日
  ];
  for (const b of bad) assert.equal(validateQuery(q(b), NOW).ok, false, JSON.stringify(b));
  const missing = { ...OK_QUERY };
  delete missing.closed_at;
  assert.equal(validateQuery(q(missing), NOW).ok, false);
});

test("締切の判定", () => {
  const closed = parseClosedAt("2026-11-03T10:45:00+09:00");
  assert.equal(isClosed(closed, closed - 1), false);
  assert.equal(isClosed(closed, closed), true);
  assert.equal(isClosed(closed, closed + 1000), true);
});

test("公式サイトの URL", () => {
  assert.equal(officialUrl("3t", "20261103", "02", 1), "https://www.boatrace.jp/owpc/pc/race/odds3t?rno=1&jcd=02&hd=20261103");
  assert.equal(officialUrl("2tf", "20261103", "24", 12), "https://www.boatrace.jp/owpc/pc/race/odds2tf?rno=12&jcd=24&hd=20261103");
  assert.equal(officialUrl("tf", "20261103", "01", 5), "https://www.boatrace.jp/owpc/pc/race/oddstf?rno=5&jcd=01&hd=20261103");
});

test("KV のキー", () => {
  assert.equal(cacheKey("20261103-02-01", "3t"), "cache:20261103-02-01:3t");
  assert.equal(countKey("20261103"), "count:20261103");
  assert.equal(stoppedKey("20261103"), "stopped:20261103");
});

test("1日の上限", () => {
  assert.equal(parseDailyLimit(undefined), DEFAULT_DAILY_LIMIT);
  assert.equal(parseDailyLimit(""), DEFAULT_DAILY_LIMIT);
  assert.equal(parseDailyLimit("abc"), DEFAULT_DAILY_LIMIT);
  assert.equal(parseDailyLimit("0"), DEFAULT_DAILY_LIMIT);
  assert.equal(parseDailyLimit("150"), 150);
  assert.equal(parseDailyLimit("5000"), MAX_DAILY_LIMIT);
  assert.equal(parseCount(null), 0);
  assert.equal(parseCount("12"), 12);
  assert.equal(parseCount("x"), 0);
  assert.equal(withinDailyLimit(297, 3, 300), true);
  assert.equal(withinDailyLimit(298, 3, 300), false);
  assert.equal(withinDailyLimit(300, 1, 300), false);
});

test("キャッシュの鮮度", () => {
  const entry = { fetched_at_ms: NOW, odds: { win: {} } };
  assert.equal(isCacheFresh(entry, NOW + 59_999), true);
  assert.equal(isCacheFresh(entry, NOW + CACHE_TTL_SEC * 1000), false);
  assert.equal(isCacheFresh(null, NOW), false);
  assert.equal(isCacheFresh({ fetched_at_ms: NOW }, NOW), false);
});

test("ページの間隔", () => {
  assert.equal(waitBeforeNext(null, NOW), 0);
  assert.equal(waitBeforeNext(NOW, NOW), 1000);
  assert.equal(waitBeforeNext(NOW, NOW + 400), 600);
  assert.equal(waitBeforeNext(NOW, NOW + 1500), 0);
});

test("timingSafeEqual", () => {
  assert.equal(timingSafeEqual("secret-key", "secret-key"), true);
  assert.equal(timingSafeEqual("secret-key", "secret-kez"), false);
  assert.equal(timingSafeEqual("secret-key", "secret-ke"), false);
  assert.equal(timingSafeEqual("", "a"), false);
  assert.equal(timingSafeEqual("a", ""), false);
  assert.equal(timingSafeEqual("", ""), true);
  assert.equal(timingSafeEqual("合言葉", "合言葉"), true);
  assert.equal(timingSafeEqual("abcabc", "abc"), false); // 繰り返しで一致しないこと
});

test("許可するオリジン", () => {
  const allowed = parseAllowedOrigins(" https://user.github.io/ ,http://localhost:8000,, http://192.168.1.10:8000");
  assert.deepEqual(allowed, ["https://user.github.io", "http://localhost:8000", "http://192.168.1.10:8000"]);
  assert.equal(isOriginAllowed("http://192.168.1.10:8000", allowed), true);
  assert.equal(isOriginAllowed("http://192.168.1.11:8000", allowed), false);
  assert.equal(isOriginAllowed("https://user.github.io.evil.example", allowed), false);
  assert.equal(isOriginAllowed(null, allowed), false);
  assert.deepEqual(parseAllowedOrigins(undefined), []);
});
