// Cron の1回分(runTick)のテスト。偽物の fetch・R2・時計を使う(公式サイト・GitHub にはアクセスしない)。
import { test } from "node:test";
import assert from "node:assert/strict";

import { runTick } from "../src/index.mjs";
import { makeBucket, syntheticHtml } from "./helpers.mjs";

const TOKEN = "ghp_TEST_TOKEN_should_not_leak";
const T0 = Date.parse("2026-11-06T10:30:00+09:00");
const LATENCY = 9000;

function race(jcd, rno, closedHHMM) {
  return {
    race_id: `20261106-${String(jcd).padStart(2, "0")}-${String(rno).padStart(2, "0")}`,
    jcd, rno, closed_at: `2026-11-06T${closedHHMM}:00+09:00`,
  };
}

function world({ races = [], upstream = {}, bucket, env = {}, start = T0 } = {}) {
  const b = bucket ?? makeBucket({ "schedule/20261106.json": { date: "2026-11-06", races } });
  let clock = start;
  const calls = [];
  const dispatches = [];
  const logs = [];
  const deps = {
    now: () => clock,
    sleep: async (ms) => { clock += ms; },
    log: (o) => logs.push(JSON.stringify(o)),
    upstreamTimeoutMs: 50,
    fetchFn: async (url, init) => {
      if (url.startsWith("https://api.github.com/")) {
        dispatches.push({ url, init, body: JSON.parse(init.body) });
        return { status: 204, text: async () => "" };
      }
      const key = /jcd=(\d+)/.exec(url)[1] + "-" + /rno=(\d+)/.exec(url)[1];
      calls.push({ url, key, start: clock });
      const spec = (typeof upstream === "function" ? upstream(key, calls.length) : upstream[key]) ?? { status: 200, html: syntheticHtml() };
      if (spec.throws) throw new Error("net");
      clock += LATENCY;
      calls[calls.length - 1].end = clock;
      return { status: spec.status, text: async () => spec.html };
    },
  };
  return {
    b, calls, dispatches, logs, deps,
    env: { DATA: b, GH_TOKEN: TOKEN, ...env },
    setClock: (ms) => { clock = ms; },
    run() { return runTick(this.env, deps); },
  };
}

test("窓の外のレースは取らない。窓の中は締切の早い順に、1.5秒以上の間隔で", async () => {
  const w = world({
    races: [race(2, 2, "10:42"), race(2, 1, "10:40"), race(3, 1, "10:50"), race(4, 1, "10:33"), race(5, 1, "10:20")],
  });
  const s = await w.run();
  // 10:40(10分前)・10:42(12分前)は対象。10:50(20分前)・10:33(3分前)・締切後は対象外
  assert.deepEqual(w.calls.map((c) => c.key), ["02-1", "02-2"]);
  assert.ok(w.calls[1].start - w.calls[0].end >= 1500);
  assert.deepEqual(s.saved, ["20261106-02-01", "20261106-02-02"]);
  const doc = w.b.json("beforeinfo/20261106/02-01.json");
  assert.equal(doc.status, "ok");
  assert.equal(doc.attempt, 1);
  assert.equal(doc.fetched_at, "2026-11-06T10:30:09+09:00");
  assert.equal(doc.minutes_before_close, 9.9);
  assert.equal(doc.boats["1"].exhibition_time, 6.71);
  assert.ok(![...w.b.store.values()].some((v) => v.includes("<td"))); // HTML は保存しない
});

test("1回の起動で最大3ページ。残りは次の起動に回す", async () => {
  const w = world({ races: [1, 2, 3, 4, 5].map((r) => race(r, 1, `10:4${r}`)) });
  await w.run();
  assert.equal(w.calls.length, 3);
});

test("取り直しはしない(未掲載でも)。4分前以降は取らない", async () => {
  const races = [race(2, 1, "10:42"), race(2, 2, "10:43")];
  const w = world({ races, upstream: { "02-1": { status: 200, html: syntheticHtml({ posted: false }) } } });
  await w.run();
  assert.equal(w.calls.length, 2);
  assert.equal(w.b.json("beforeinfo/20261106/02-01.json").status, "not_posted");
  w.setClock(Date.parse("2026-11-06T10:35:00+09:00"));
  await w.run();
  assert.equal(w.calls.length, 2);
});

test("3ページの上限で回されたレースは、次の起動で(5分前までに)取る", async () => {
  const w = world({ races: [1, 2, 3, 4].map((r) => race(r, 1, "10:41")) });
  await w.run();
  assert.equal(w.calls.length, 3);
  w.setClock(T0 + 5 * 60_000); // 6分前
  await w.run();
  assert.deepEqual(w.calls.map((c) => c.key), ["01-1", "02-1", "03-1", "04-1"]);
});

test("dispatch は1回の起動で1回だけ、race_ids をまとめて。4分以内は送らず次に回す", async () => {
  const w = world({ races: [race(2, 1, "10:41"), race(2, 2, "10:42"), race(2, 3, "10:46")] });
  await w.run();
  assert.equal(w.dispatches.length, 1);
  const d = w.dispatches[0];
  assert.match(d.url, /\/repos\/misaki777\/boatrace-yoso\/actions\/workflows\/daily\.yml\/dispatches$/);
  assert.equal(d.init.headers.Authorization, `Bearer ${TOKEN}`);
  assert.deepEqual(d.body, { ref: "main", inputs: { mode: "lock", race_ids: "20261106-02-01,20261106-02-02", date: "", deploy: true } });
  // 3分後: 02-3 が窓に入り保存されるが、前回から4分未満なので送らない
  w.setClock(T0 + 3 * 60_000);
  const s2 = await w.run();
  assert.deepEqual(s2.saved, ["20261106-02-03"]);
  assert.equal(w.dispatches.length, 1);
  // さらに5分後: 新しい保存が無くても、残っていた分を送る
  w.setClock(T0 + 8 * 60_000);
  await w.run();
  assert.equal(w.dispatches.length, 2);
  assert.equal(w.dispatches[1].body.inputs.race_ids, "20261106-02-03");
});

test("保存が無ければ dispatch しない。GH_TOKEN が無ければ送らず残す", async () => {
  const w = world({ races: [race(2, 1, "10:50")] });
  await w.run();
  assert.equal(w.dispatches.length, 0);
  const w2 = world({ races: [race(2, 1, "10:41")], env: { GH_TOKEN: "" } });
  await w2.run();
  assert.equal(w2.dispatches.length, 0);
  assert.deepEqual(w2.b.json("beforeinfo/20261106/_state.json").pending_dispatch, ["20261106-02-01"]);
});

test("公式の 403・429 でその日を止め、以後は取らない", async () => {
  for (const status of [403, 429]) {
    const w = world({ races: [race(2, 1, "10:41"), race(2, 2, "10:42")], upstream: { "02-1": { status, html: "" } } });
    const s = await w.run();
    assert.equal(w.calls.length, 1);
    assert.ok(w.b.json("beforeinfo/20261106/_stopped.json").reason.includes(String(status)));
    assert.equal(s.result, "stopped_now");
    await w.run();
    assert.equal(w.calls.length, 1);
  }
});

test("3回続けて失敗したら止める(起動をまたいで数える)", async () => {
  const races = [race(2, 1, "10:41"), race(2, 2, "10:42"), race(2, 3, "10:43"), race(2, 4, "10:44")];
  const w = world({ races, upstream: () => ({ status: 500, html: "" }) });
  await w.run(); // 3件取って3件失敗 → 停止
  assert.equal(w.calls.length, 3);
  assert.ok(w.b.json("beforeinfo/20261106/_stopped.json"));
  const w2 = world({ races, upstream: (k) => (k === "02-3" ? { status: 200, html: "<html>壊れた</html>" } : { status: 503, html: "" }) });
  await w2.run();
  assert.ok(w2.b.json("beforeinfo/20261106/_stopped.json"));
});

test("1日の上限(既定300)に達したら、その日は止める", async () => {
  const st = { count: 300, consecutive_failures: 0, last_dispatch_ms: null, pending_dispatch: [], races: {} };
  const sched = { date: "2026-11-06", races: [race(2, 2, "10:41")] };
  const w = world({ bucket: makeBucket({ "schedule/20261106.json": sched, "beforeinfo/20261106/_state.json": st }) });
  const s = await w.run();
  assert.equal(w.calls.length, 0);
  assert.ok(w.b.json("beforeinfo/20261106/_stopped.json"));
  assert.equal(s.result, "stopped_now");
  const w2 = world({ races: [race(2, 1, "10:41"), race(2, 2, "10:42")], env: { DAILY_LIMIT: "1" } });
  await w2.run();
  assert.equal(w2.calls.length, 1);
  assert.ok(w2.b.json("beforeinfo/20261106/_stopped.json"));
});

test("schedule が無い・停止中ならすぐ終わる", async () => {
  const w = world({ bucket: makeBucket({}) });
  assert.equal((await w.run()).result, "no_schedule");
  const w2 = world({ races: [race(2, 1, "10:41")] });
  w2.b.store.set("beforeinfo/20261106/_stopped.json", '{"reason":"manual"}');
  assert.equal((await w2.run()).result, "stopped");
  assert.equal(w2.calls.length, 0);
});

test("ログにトークン・展示タイムなどの値・HTML を出さない", async () => {
  const w = world({ races: [race(2, 1, "10:41"), race(2, 2, "10:42")], upstream: { "02-2": { status: 500, html: "" } } });
  await w.run();
  const all = w.logs.join("\n");
  assert.ok(!all.includes(TOKEN) && !all.includes("ghp_"));
  assert.ok(!/6\.71|exhibition|start_timing|weather|racer_number|9001|<td/.test(all));
  assert.match(all, /20261106-02-01/);
});

test("ページごとのログに status と締切までの分を残す(値は出さない)", async () => {
  const w = world({ races: [race(2, 1, "10:42")], env: { WINDOW_START_MIN: "20", WINDOW_END_MIN: "12" } });
  await w.run();
  const page = w.logs.map((l) => JSON.parse(l)).find((l) => l.event === "page");
  assert.equal(page.status, "ok");
  assert.equal(page.minutes_before_close, 11.9);
  const w2 = world({ races: [race(2, 1, "10:50")], env: { WINDOW_START_MIN: "20", WINDOW_END_MIN: "15" } });
  await w2.run();
  assert.equal(w2.calls.length, 1); // 20分前は既定の窓の外だが、設定で取る
});
