// 解析1ページあたりの所要時間を測る(spec/07 9.4。Workers の CPU 時間 10ms/要求 に収まるかの目安)。
//
// 使い方(リポジトリ直下で): node workers/odds-proxy/scripts/bench_parse.mjs
//
// data/raw/official_odds/ に実物の HTML(約35〜46KB)があればそれを、無ければ共通の合成HTMLを使う。
// Workers の中では performance.now() が CPU の実行中に進まない(タイミング攻撃の対策)ので、
// 実際の CPU 時間は Cloudflare のダッシュボード(Workers → Metrics → CPU Time)で確かめる。

import fs from "node:fs";
import path from "node:path";
import zlib from "node:zlib";
import { fileURLToPath } from "node:url";
import { performance } from "node:perf_hooks";

import { parsePage } from "../src/parse.mjs";

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");
const TYPE_BY_PAGE = { odds3t: "3t", odds2tf: "2tf", oddstf: "tf" };

function collect() {
  const raw = path.join(REPO, "data/raw/official_odds");
  const found = [];
  const walk = (dir) => {
    for (const ent of fs.readdirSync(dir, { withFileTypes: true })) {
      const p = path.join(dir, ent.name);
      if (ent.isDirectory()) walk(p);
      else {
        const m = /_(odds3t|odds2tf|oddstf)_T\w+\.html\.gz$/.exec(ent.name);
        if (m) found.push({ name: ent.name, type: TYPE_BY_PAGE[m[1]], html: zlib.gunzipSync(fs.readFileSync(p)).toString("utf8") });
      }
    }
  };
  if (fs.existsSync(raw)) walk(raw);
  if (found.length > 0) return { source: "実物(data/raw)", files: found };
  const fx = path.join(REPO, "tests/fixtures/official_odds");
  for (const page of Object.keys(TYPE_BY_PAGE)) {
    const p = path.join(fx, `${page}.synthetic.html`);
    if (fs.existsSync(p)) found.push({ name: path.basename(p), type: TYPE_BY_PAGE[page], html: fs.readFileSync(p, "utf8") });
  }
  return { source: "合成HTML", files: found };
}

const { source, files } = collect();
const N = 300;
console.log(`対象: ${source}、各 ${N} 回(最初の50回は慣らしとして除外)`);
for (const f of files) {
  const times = [];
  let firstMs = null;
  for (let i = 0; i < N + 50; i++) {
    const t0 = performance.now();
    parsePage(f.type, f.html);
    const dt = performance.now() - t0;
    if (i === 0) firstMs = dt;
    if (i >= 50) times.push(dt);
  }
  times.sort((a, b) => a - b);
  const median = times[Math.floor(times.length / 2)];
  const p95 = times[Math.floor(times.length * 0.95)];
  console.log(
    `${f.name.padEnd(30)} ${String(Math.round(f.html.length / 1024)).padStart(3)}KB  ` +
      `初回 ${firstMs.toFixed(3)}ms  中央値 ${median.toFixed(3)}ms  95% ${p95.toFixed(3)}ms`,
  );
}
