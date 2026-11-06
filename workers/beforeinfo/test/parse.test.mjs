// 解析のテスト。共通データ(C-8)があればそれで Python との一致を、無ければ仮の構造で確かめる。
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { BeforeinfoParseError, intOrNull, parseBeforeinfo, parseParts, parseStartTiming, toNumber } from "../src/parse.mjs";
import { syntheticHtml } from "./helpers.mjs";

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");
const FIXTURES = path.join(REPO, "tests/fixtures/beforeinfo");
const names = fs.existsSync(FIXTURES) ? fs.readdirSync(FIXTURES).sort() : [];
const synthetic = names.filter((n) => n.endsWith(".synthetic.html"));
const broken = names.filter((n) => n.startsWith("broken_") && n.endsWith(".html"));

test("共通の合成HTML(C-8)がある", { skip: synthetic.length === 0 && "tests/fixtures/beforeinfo/ がまだ無い(Python との一致は未確認)" }, () => {
  assert.ok(synthetic.length > 0);
});

for (const name of synthetic) {
  test(`共通の合成HTMLが期待値と一致する: ${name}`, () => {
    const html = fs.readFileSync(path.join(FIXTURES, name), "utf8");
    const expected = JSON.parse(fs.readFileSync(path.join(FIXTURES, name.replace(/\.synthetic\.html$/, ".expected.json")), "utf8"));
    const got = parseBeforeinfo(html);
    for (const k of ["status", "weather", "boats"]) if (k in expected) assert.deepEqual(got[k], expected[k], k);
  });
}
for (const name of broken) {
  test(`壊れた例は失敗する: ${name}`, () => {
    assert.throws(() => parseBeforeinfo(fs.readFileSync(path.join(FIXTURES, name), "utf8")), BeforeinfoParseError);
  });
}

test("数値・ST・部品の変換(Python と同じ規則)", () => {
  assert.equal(toNumber("52.0kg"), 52.0);
  assert.equal(toNumber("22.0℃"), 22.0);
  assert.equal(toNumber("-0.5"), -0.5);
  assert.equal(toNumber(""), null);
  assert.deepEqual(parseStartTiming(".17"), [0.17, false]);
  assert.deepEqual(parseStartTiming("F.03"), [-0.03, true]);
  assert.deepEqual(parseStartTiming(""), [null, false]);
  assert.deepEqual(parseStartTiming("L"), [null, false]);
  assert.equal(intOrNull(2.5), 2); // 偶数への丸め(Python の round)
  assert.equal(intOrNull(3.5), 4);
  assert.equal(intOrNull(null), null);
  assert.deepEqual(parseParts("<ul><li>リング×２</li><li><span>ピストン</span></li><li>謎</li></ul>"), [
    { number_source: "リング", number: 2, quantity: 2 },
    { number_source: "ピストン", number: 1, quantity: null },
    { number_source: "謎", number: null, quantity: null },
  ]);
});

test("表が無い・tbody が足りないと失敗", () => {
  assert.throws(() => parseBeforeinfo("<html>メンテナンス中</html>"), BeforeinfoParseError);
  const five = syntheticHtml().replace(/<tbody\b[^>]*>[\s\S]*?<\/tbody>/, "");
  assert.throws(() => parseBeforeinfo(five), BeforeinfoParseError);
});
