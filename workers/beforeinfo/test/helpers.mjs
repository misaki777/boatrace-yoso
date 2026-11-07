// テスト用の偽物と、共通の合成HTML(収集担当 C-8 の tests/fixtures/beforeinfo/)の読み込み。
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

export const FIXTURES = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../../tests/fixtures/beforeinfo");

/** 共通の合成HTML。posted=false で未掲載の例(not_posted.synthetic.html)、既定は ok.synthetic.html。 */
export function syntheticHtml({ posted = true } = {}) {
  return fs.readFileSync(path.join(FIXTURES, posted ? "ok.synthetic.html" : "not_posted.synthetic.html"), "utf8");
}

const ODDS_FIXTURES = path.resolve(FIXTURES, "../official_odds");
/** 締切前オッズの共通の合成HTML(tests/fixtures/official_odds/)。 */
export function oddsHtml(type) {
  const page = { tf: "oddstf", "2tf": "odds2tf", "3t": "odds3t" }[type];
  return fs.readFileSync(path.join(ODDS_FIXTURES, `${page}.synthetic.html`), "utf8");
}

/** R2 の偽物(get → {text()} | null、put、head)。 */
export function makeBucket(initial = {}) {
  const store = new Map(Object.entries(initial).map(([k, v]) => [k, typeof v === "string" ? v : JSON.stringify(v)]));
  const puts = [];
  return {
    store,
    puts,
    async get(key) {
      return store.has(key) ? { text: async () => store.get(key) } : null;
    },
    async head(key) {
      return store.has(key) ? {} : null;
    },
    async put(key, value) {
      puts.push(key);
      store.set(key, value);
    },
    json(key) {
      return store.has(key) ? JSON.parse(store.get(key)) : null;
    },
  };
}
