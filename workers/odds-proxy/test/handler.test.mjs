// fetch ハンドラのテスト。Node 16 には fetch・Request・Response が無いので、
// 偽物の fetch・KV・時計・応答の作り方を渡して確かめる(公式サイトにはアクセスしない)。

import { test } from "node:test";
import assert from "node:assert/strict";

import { handleRequest } from "../src/index.mjs";

const KEY = "test-secret-key-123";
const ORIGIN = "http://192.168.1.10:8000";
const T0 = Date.parse("2026-11-03T10:30:00+09:00");
const CLOSED_AT = "2026-11-03T10:45:00+09:00";
const LATENCY_MS = 9000; // 公式サイトの応答にかかる時間(偽物)

// ---------------------------------------------------------------------
// 偽物
// ---------------------------------------------------------------------

/** 架空の数値の HTML を作る(ログに出ていないかを調べやすい、目立つ値にする)。 */
function fakeHtml(type, { label = "オッズ更新時間 10:29", value = "777.7" } = {}) {
  const n = { "3t": 120, "2tf": 45, tf: 12 }[type];
  const cells = [];
  for (let i = 0; i < n; i++) {
    const v = type === "tf" && i >= 6 ? `${value}-888.8` : value;
    cells.push(`<tr><td class="is-boatColor1">1</td><td class="oddsPoint ">${v}</td></tr>`);
  }
  const labelHtml = label.includes("締切時")
    ? `<p class="tab4_time">${label}</p>`
    : `<p class="tab4_refreshText">\n ${label}\n</p>`;
  return `<html><body><div class="table1 h-mt10"><table><tr><td>出走表</td></tr></table></div>
${labelHtml}<div class="table1"><table>${cells.join("\n")}</table></div></body></html>`;
}

function makeKv(initial = {}) {
  const store = new Map(Object.entries(initial));
  const puts = [];
  return {
    store,
    puts,
    async get(key, opts) {
      if (!store.has(key)) return null;
      const v = store.get(key);
      return opts && opts.type === "json" ? JSON.parse(v) : v;
    },
    async put(key, value, opts) {
      puts.push({ key, value, opts });
      store.set(key, value);
    },
  };
}

/**
 * 偽物の環境一式。upstream は type → {status, html} か、関数 (url) => {status, html}。
 */
function makeWorld({ upstream = {}, kv = makeKv(), env = {} } = {}) {
  let clock = T0;
  const calls = [];
  const logs = [];
  const deps = {
    now: () => clock,
    sleep: async (ms) => {
      clock += ms;
    },
    log: (obj) => logs.push(JSON.stringify(obj)),
    makeResponse: (body, init) => ({ status: init.status, headers: init.headers, body }),
    upstreamTimeoutMs: 50,
    fetchFn: async (url, init) => {
      const type = { odds3t: "3t", odds2tf: "2tf", oddstf: "tf" }[/race\/(\w+)\?/.exec(url)[1]];
      calls.push({ url, type, startMs: clock, init });
      const spec = typeof upstream === "function" ? upstream(url, type) : upstream[type] ?? { status: 200, html: fakeHtml(type) };
      if (spec.hang) return new Promise(() => {}); // 応答が返らない(タイムアウト)
      if (spec.throws) throw new Error("network");
      clock += LATENCY_MS;
      calls[calls.length - 1].endMs = clock;
      return { status: spec.status, text: async () => spec.html };
    },
  };
  const fullEnv = {
    ODDS_KEY: KEY,
    ALLOWED_ORIGINS: `https://user.github.io,http://localhost:8000,${ORIGIN}`,
    ODDS_KV: kv,
    ...env,
  };
  return {
    deps,
    env: fullEnv,
    kv,
    calls,
    logs,
    advance: (ms) => {
      clock += ms;
    },
  };
}

function makeRequest({ method = "GET", path = "/odds", query = {}, headers = {} } = {}) {
  const params = new URLSearchParams({ date: "20261103", jcd: "02", rno: "1", closed_at: CLOSED_AT, ...query });
  for (const [k, v] of Object.entries(query)) if (v === undefined) params.delete(k);
  const h = { Origin: ORIGIN, "X-Odds-Key": KEY, ...headers };
  const lower = {};
  for (const [k, v] of Object.entries(h)) if (v !== undefined) lower[k.toLowerCase()] = v;
  return {
    method,
    url: `https://odds-proxy.example.workers.dev${path}?${params}`,
    headers: { get: (name) => (name.toLowerCase() in lower ? lower[name.toLowerCase()] : null) },
  };
}

async function call(world, req) {
  const res = await handleRequest(req, world.env, world.deps);
  return { ...res, json: res.body ? JSON.parse(res.body) : null };
}

// ---------------------------------------------------------------------
// 正常系
// ---------------------------------------------------------------------

test("200: 3ページを順番に取り、odds をまとめて返す", async () => {
  const w = makeWorld();
  const res = await call(w, makeRequest());
  assert.equal(res.status, 200);
  assert.equal(res.headers["Access-Control-Allow-Origin"], ORIGIN);
  assert.equal(res.headers["Cache-Control"], "no-store");
  const b = res.json;
  assert.equal(b.ok, true);
  assert.equal(b.race_id, "20261103-02-01");
  assert.equal(b.cached, false);
  assert.equal(b.cache_age_sec, 0);
  assert.deepEqual(b.pages, { "3t": "ok", "2tf": "ok", tf: "ok" });
  assert.equal(b.odds_time_label, "オッズ更新時間 10:29");
  assert.equal(b.is_final_odds, false);
  assert.deepEqual(Object.keys(b.odds).sort(), ["exacta", "place", "quinella", "trifecta", "win"]);
  assert.equal(b.odds.trifecta["2"]["1"]["6"], 777.7);
  assert.deepEqual(b.odds.place["1"], { lower_limit: 777.7, upper_limit: 888.8 });
  assert.equal(b.fetched_at, "2026-11-03T10:30:09+09:00"); // 最初のページの応答の時刻
  assert.deepEqual(b.quota, { used_today: 3, limit_per_day: 300 });
  // 取得順と URL・User-Agent
  assert.deepEqual(w.calls.map((c) => c.type), ["3t", "2tf", "tf"]);
  assert.match(w.calls[0].url, /^https:\/\/www\.boatrace\.jp\/owpc\/pc\/race\/odds3t\?rno=1&jcd=02&hd=20261103$/);
  assert.equal(w.calls[0].init.headers["User-Agent"], "boatrace-yoso-proxy/0.1 (personal use)");
  // KV: キャッシュ3つ(60秒)+ 使用数1つ
  const cachePuts = w.kv.puts.filter((p) => p.key.startsWith("cache:"));
  assert.equal(cachePuts.length, 3);
  assert.ok(cachePuts.every((p) => p.opts.expirationTtl === 60));
  assert.ok(cachePuts.every((p) => !p.value.includes("<td"))); // HTML は入れない
  assert.equal(w.kv.store.get("count:20261103"), "3");
  assert.equal(w.kv.puts.filter((p) => p.key.startsWith("count:")).length, 1);
});

test("ページの間を1秒以上空け、並列に取らない", async () => {
  const w = makeWorld();
  await call(w, makeRequest());
  assert.equal(w.calls.length, 3);
  for (let i = 1; i < w.calls.length; i++) {
    assert.ok(w.calls[i].startMs - w.calls[i - 1].endMs >= 1000, `gap ${i}`);
    assert.ok(w.calls[i].startMs >= w.calls[i - 1].endMs); // 前の応答が終わってから
  }
});

test("types で券種を絞る", async () => {
  const w = makeWorld();
  const res = await call(w, makeRequest({ query: { types: "tf" } }));
  assert.equal(res.status, 200);
  assert.deepEqual(res.json.pages, { tf: "ok" });
  assert.deepEqual(Object.keys(res.json.odds).sort(), ["place", "win"]);
  assert.equal(w.calls.length, 1);
  assert.equal(res.json.quota.used_today, 1);
});

test("キャッシュ中(60秒以内)は公式に取りに行かず、上限にも数えない", async () => {
  const w = makeWorld();
  await call(w, makeRequest());
  assert.equal(w.calls.length, 3);
  w.advance(20_000);
  const res = await call(w, makeRequest());
  assert.equal(w.calls.length, 3); // 増えていない
  assert.equal(res.status, 200);
  assert.deepEqual(res.json.pages, { "3t": "cached", "2tf": "cached", tf: "cached" });
  assert.equal(res.json.cached, true);
  assert.equal(res.json.fetched_at, "2026-11-03T10:30:09+09:00"); // 元の取得時刻
  assert.ok(res.json.cache_age_sec >= 20);
  assert.equal(res.json.quota.used_today, 3);
  assert.equal(res.json.odds.trifecta["1"]["2"]["3"], 777.7);
});

test("キャッシュが60秒を過ぎたら取り直す(KV の TTL が延びても時刻で判定する)", async () => {
  const w = makeWorld();
  await call(w, makeRequest({ query: { types: "tf" } }));
  w.advance(61_000);
  const res = await call(w, makeRequest({ query: { types: "tf" } }));
  assert.equal(w.calls.length, 2);
  assert.deepEqual(res.json.pages, { tf: "ok" });
  assert.equal(res.json.quota.used_today, 2);
});

test("一部だけキャッシュ: 残りだけ取る", async () => {
  const w = makeWorld();
  await call(w, makeRequest({ query: { types: "tf" } }));
  w.advance(5_000);
  const res = await call(w, makeRequest());
  assert.deepEqual(w.calls.map((c) => c.type), ["tf", "3t", "2tf"]);
  assert.deepEqual(res.json.pages, { "3t": "ok", "2tf": "ok", tf: "cached" });
  assert.equal(res.json.cached, true);
  assert.equal(res.json.quota.used_today, 3);
});

test("締切時オッズの表記を見たら is_final_odds=true にし、残りのページは取らない", async () => {
  const w = makeWorld({ upstream: { "3t": { status: 200, html: fakeHtml("3t", { label: "締切時オッズ" }) } } });
  const res = await call(w, makeRequest());
  assert.equal(res.status, 200);
  assert.equal(res.json.is_final_odds, true);
  assert.equal(res.json.odds_time_label, "締切時オッズ");
  assert.deepEqual(res.json.pages, { "3t": "ok", "2tf": "skipped", tf: "skipped" });
  assert.equal(w.calls.length, 1);
  assert.equal(res.json.quota.used_today, 1);
});

// ---------------------------------------------------------------------
// 一部失敗・失敗
// ---------------------------------------------------------------------

test("一部失敗は 200 で、失敗した券種は odds に入れない(5xx・解析失敗・タイムアウト)", async () => {
  const w = makeWorld({
    upstream: {
      "3t": { status: 503, html: "" },
      "2tf": { status: 200, html: "<html>メンテナンス中</html>" },
    },
  });
  const res = await call(w, makeRequest());
  assert.equal(res.status, 200);
  assert.deepEqual(res.json.pages, { "3t": "error", "2tf": "parse_error", tf: "ok" });
  assert.deepEqual(Object.keys(res.json.odds).sort(), ["place", "win"]);
  assert.equal(res.json.quota.used_today, 3); // 失敗も公式への要求として数える
  assert.equal(w.kv.puts.filter((p) => p.key.startsWith("cache:")).length, 1); // 失敗はキャッシュしない

  const w2 = makeWorld({ upstream: { tf: { hang: true }, "2tf": { throws: true } } });
  const res2 = await call(w2, makeRequest());
  assert.equal(res2.status, 200);
  assert.deepEqual(res2.json.pages, { "3t": "ok", "2tf": "error", tf: "error" });
});

test("502: どのページも取れなかった", async () => {
  const w = makeWorld({ upstream: () => ({ status: 500, html: "" }) });
  const res = await call(w, makeRequest());
  assert.equal(res.status, 502);
  assert.equal(res.json.ok, false);
  assert.equal(res.json.error, "upstream_error");
  assert.deepEqual(res.json.pages, { "3t": "error", "2tf": "error", tf: "error" });
  assert.equal(res.json.quota.used_today, 3);
});

test("公式の 403・429 で停止フラグを書き、503。以後その日は取りに行かない", async () => {
  for (const status of [403, 429]) {
    const w = makeWorld({ upstream: { "2tf": { status, html: "" } } });
    const res = await call(w, makeRequest());
    assert.equal(res.status, 503);
    assert.equal(res.json.error, "stopped");
    assert.deepEqual(w.calls.map((c) => c.type), ["3t", "2tf"]); // tf は取らない
    const stopped = w.kv.puts.find((p) => p.key === "stopped:20261103");
    assert.ok(stopped);
    assert.ok(stopped.opts.expirationTtl > 0);
    assert.equal(w.kv.store.get("count:20261103"), "2");
    // 次の要求(別のレース)は公式に行かずに 503
    const res2 = await call(w, makeRequest({ query: { rno: "2" } }));
    assert.equal(res2.status, 503);
    assert.equal(w.calls.length, 2);
  }
});

test("503: 停止中は取りに行かない", async () => {
  const w = makeWorld({ kv: makeKv({ "stopped:20261103": '{"status":403}' }) });
  const res = await call(w, makeRequest());
  assert.equal(res.status, 503);
  assert.equal(res.json.error, "stopped");
  assert.equal(w.calls.length, 0);
});

test("前日の停止フラグは今日に影響しない", async () => {
  const w = makeWorld({ kv: makeKv({ "stopped:20261102": '{"status":403}' }) });
  const res = await call(w, makeRequest());
  assert.equal(res.status, 200);
});

test("429: 1日の上限に達した(キャッシュ分は数えない)", async () => {
  const w = makeWorld({ kv: makeKv({ "count:20261103": "299" }) });
  const res = await call(w, makeRequest());
  assert.equal(res.status, 429);
  assert.equal(res.json.error, "daily_limit");
  assert.deepEqual(res.json.quota, { used_today: 299, limit_per_day: 300 });
  assert.equal(w.calls.length, 0);
  // 1ページなら収まる
  const res2 = await call(w, makeRequest({ query: { types: "tf" } }));
  assert.equal(res2.status, 200);
  assert.equal(w.kv.store.get("count:20261103"), "300");
  // 上限ちょうどでも、キャッシュにあるものは返せる
  const res3 = await call(w, makeRequest({ query: { types: "tf" } }));
  assert.equal(res3.status, 200);
  assert.deepEqual(res3.json.pages, { tf: "cached" });
  const res4 = await call(w, makeRequest({ query: { types: "3t" } }));
  assert.equal(res4.status, 429);
});

test("DAILY_LIMIT の環境変数", async () => {
  const w = makeWorld({ env: { DAILY_LIMIT: "2" } });
  const res = await call(w, makeRequest());
  assert.equal(res.status, 429);
  assert.equal(res.json.quota.limit_per_day, 2);
});

// ---------------------------------------------------------------------
// 認証・CORS・入力
// ---------------------------------------------------------------------

test("401: 合言葉が無い・違う", async () => {
  const w = makeWorld();
  for (const key of [undefined, "", "wrong", KEY + "x", KEY.slice(0, -1)]) {
    const res = await call(w, makeRequest({ headers: { "X-Odds-Key": key } }));
    assert.equal(res.status, 401, String(key));
    assert.equal(res.json.error, "unauthorized");
    assert.equal(res.headers["Access-Control-Allow-Origin"], ORIGIN); // ブラウザがエラーを読めるように
  }
  assert.equal(w.calls.length, 0);
});

test("403: 許可していないオリジン(Access-Control-Allow-Origin を付けない)", async () => {
  const w = makeWorld();
  for (const origin of ["https://evil.example", "http://192.168.1.11:8000", "null"]) {
    const res = await call(w, makeRequest({ headers: { Origin: origin } }));
    assert.equal(res.status, 403);
    assert.equal(res.json.error, "forbidden_origin");
    assert.equal(res.headers["Access-Control-Allow-Origin"], undefined);
  }
  assert.equal(w.calls.length, 0);
});

test("Origin の無い要求(curl など)は合言葉だけで通る", async () => {
  const w = makeWorld();
  const res = await call(w, makeRequest({ headers: { Origin: undefined }, query: { types: "tf" } }));
  assert.equal(res.status, 200);
  assert.equal(res.headers["Access-Control-Allow-Origin"], undefined);
});

test("プリフライト(OPTIONS): 許可するヘッダは X-Odds-Key だけ", async () => {
  const w = makeWorld();
  const res = await call(w, makeRequest({ method: "OPTIONS", headers: { "X-Odds-Key": undefined } }));
  assert.equal(res.status, 204);
  assert.equal(res.headers["Access-Control-Allow-Origin"], ORIGIN);
  assert.equal(res.headers["Access-Control-Allow-Headers"], "X-Odds-Key");
  assert.match(res.headers["Access-Control-Allow-Methods"], /GET/);
  const bad = await call(w, makeRequest({ method: "OPTIONS", headers: { Origin: "https://evil.example" } }));
  assert.equal(bad.status, 403);
});

test("400: パラメータの誤り・今日以外の日付", async () => {
  const w = makeWorld();
  const cases = [
    { date: "20261102" },
    { date: "20261104" },
    { jcd: "25" },
    { rno: "13" },
    { types: "3f" },
    { closed_at: "abc" },
    { closed_at: undefined },
  ];
  for (const query of cases) {
    const res = await call(w, makeRequest({ query }));
    assert.equal(res.status, 400, JSON.stringify(query));
    assert.equal(res.json.error, "bad_request");
    assert.ok(res.json.message.length > 0);
  }
  assert.equal(w.calls.length, 0);
});

test("409: 締切を過ぎた要求は公式に取りに行かない", async () => {
  const w = makeWorld();
  for (const closed of ["2026-11-03T10:30:00+09:00", "2026-11-03T10:00:00+09:00"]) {
    const res = await call(w, makeRequest({ query: { closed_at: closed } }));
    assert.equal(res.status, 409);
    assert.equal(res.json.error, "closed");
  }
  assert.equal(w.calls.length, 0);
});

test("404・405・500(設定不足)", async () => {
  const w = makeWorld();
  assert.equal((await call(w, makeRequest({ path: "/" }))).status, 404);
  assert.equal((await call(w, makeRequest({ method: "POST" }))).status, 405);
  const w2 = makeWorld({ env: { ODDS_KEY: "" } });
  const r = await call(w2, makeRequest());
  assert.equal(r.status, 500);
  assert.equal(r.json.error, "server_misconfigured");
  const w3 = makeWorld({ env: { ODDS_KV: undefined } });
  assert.equal((await call(w3, makeRequest())).status, 500);
});

// ---------------------------------------------------------------------
// ログ
// ---------------------------------------------------------------------

test("ログにオッズの数値・合言葉・HTML を出さない", async () => {
  const w = makeWorld({ upstream: { "2tf": { status: 200, html: "<html>壊れた</html>" } } });
  await call(w, makeRequest());
  w.advance(10_000);
  await call(w, makeRequest()); // キャッシュから
  await call(w, makeRequest({ headers: { "X-Odds-Key": "wrong" } }));
  await call(w, makeRequest({ query: { closed_at: "2026-11-03T10:00:00+09:00" } }));
  const w2 = makeWorld({ upstream: { "3t": { status: 403, html: "" } } });
  await call(w2, makeRequest());
  const all = [...w.logs, ...w2.logs].join("\n");
  assert.ok(w.logs.length >= 3);
  assert.ok(!all.includes("777.7") && !all.includes("888.8"), "オッズの数値");
  assert.ok(!all.includes(KEY), "合言葉");
  assert.ok(!all.includes("<"), "HTML");
  assert.ok(!/trifecta|exacta|quinella|"win"|"place"/.test(all), "odds の中身");
  // 出すべきもの
  assert.match(all, /20261103-02-01/);
  assert.match(all, /used_today/);
  assert.match(all, /elapsed_ms/);
});
