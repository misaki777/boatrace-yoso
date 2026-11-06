import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { CRON_MODES, modeForCron, dispatchRequest, dispatch } from "../src/index.mjs";

test("cron からモードを決める", () => {
  assert.equal(modeForCron("41 22 * * *"), "morning");
  assert.equal(modeForCron("4,34 23,0-14 * * *"), "daytime");
  assert.equal(modeForCron("12 16 * * *"), "finalize");
  assert.equal(modeForCron("0 18 * * SUN"), "weekly");
  assert.equal(modeForCron("* * * * *"), null);
});

test("wrangler.toml の crons と CRON_MODES が一致する", () => {
  const toml = readFileSync(new URL("../wrangler.toml", import.meta.url), "utf8");
  const m = toml.match(/crons\s*=\s*\[([^\]]*)\]/);
  const crons = [...m[1].matchAll(/"([^"]+)"/g)].map((x) => x[1]).sort();
  assert.deepEqual(crons, Object.keys(CRON_MODES).sort());
});

test("workflow_dispatch の要求の形", () => {
  const { url, init } = dispatchRequest("daytime", "TOKEN");
  assert.equal(url, "https://api.github.com/repos/misaki777/boatrace-yoso/actions/workflows/daily.yml/dispatches");
  assert.equal(init.method, "POST");
  assert.equal(init.headers.Authorization, "Bearer TOKEN");
  assert.deepEqual(JSON.parse(init.body), { ref: "main", inputs: { mode: "daytime", date: "", deploy: true } });
});

test("トークンが無ければ呼ばない", async () => {
  let called = false;
  const r = await dispatch("morning", {}, async () => { called = true; });
  assert.equal(r.ok, false);
  assert.equal(called, false);
});

test("204 で成功、ログにトークンを出さない", async () => {
  const logs = [];
  const orig = console.log;
  console.log = (s) => logs.push(String(s));
  try {
    const r = await dispatch("finalize", { GH_TOKEN: "secret-xyz" }, async () => new Response(null, { status: 204 }));
    assert.equal(r.ok, true);
  } finally {
    console.log = orig;
  }
  assert.ok(!logs.join("\n").includes("secret-xyz"));
});
