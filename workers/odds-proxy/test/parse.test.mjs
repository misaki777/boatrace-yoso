// HTML 解析のテスト(spec/07 2.2、9.4、9.5)。ネットワークは使わない。
//
// - tests/fixtures/official_odds/ の共通の合成HTMLと期待値(収集担当が作る)で、Python と同じ結果になること
// - data/raw/official_odds/ に実物の HTML と Python の解析結果があれば、それとも一致すること(無ければ skip)

import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import zlib from "node:zlib";
import { fileURLToPath } from "node:url";

import {
  OddsParseError,
  extractCells,
  extractTimeLabel,
  isFinalLabel,
  parseExactaQuinella,
  parsePage,
  parseTrifecta,
  parseWinPlace,
  toOdds,
  toRange,
} from "../src/parse.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(HERE, "../../..");
const FIXTURES = path.join(REPO, "tests/fixtures/official_odds");
const RAW = path.join(REPO, "data/raw/official_odds");
const TYPE_BY_PAGE = { odds3t: "3t", odds2tf: "2tf", oddstf: "tf" };

function pageOf(name) {
  const m = /(odds3t|odds2tf|oddstf)/.exec(name);
  return m ? m[1] : null;
}

// ---------------------------------------------------------------------
// 共通の合成HTML(C-6)
// ---------------------------------------------------------------------

const fixtureNames = fs.existsSync(FIXTURES) ? fs.readdirSync(FIXTURES).sort() : [];
const syntheticNames = fixtureNames.filter((n) => n.endsWith(".synthetic.html"));
const brokenNames = fixtureNames.filter((n) => n.startsWith("broken_") && n.endsWith(".html"));

test("共通の合成HTMLがある(無ければ R-2 は未確認)", { skip: syntheticNames.length === 0 && "tests/fixtures/official_odds/ がまだ無い" }, () => {
  const pages = new Set(syntheticNames.map(pageOf));
  assert.deepEqual([...pages].sort(), ["odds2tf", "odds3t", "oddstf"]);
});

for (const name of syntheticNames) {
  test(`合成HTMLが期待値と一致する: ${name}`, () => {
    const page = pageOf(name);
    const html = fs.readFileSync(path.join(FIXTURES, name), "utf8");
    const expectedPath = path.join(FIXTURES, name.replace(/\.synthetic\.html$/, ".expected.json"));
    const expected = JSON.parse(fs.readFileSync(expectedPath, "utf8"));
    const got = parsePage(TYPE_BY_PAGE[page], html);
    if (expected.odds !== undefined) {
      assert.deepEqual(got.odds, expected.odds);
      if ("odds_time_label" in expected) assert.equal(got.label, expected.odds_time_label);
      if ("is_final_odds" in expected) assert.equal(isFinalLabel(got.label), expected.is_final_odds);
    } else {
      assert.deepEqual(got.odds, expected);
    }
  });
}

for (const name of brokenNames) {
  test(`壊れた例は解析に失敗する: ${name}`, () => {
    const html = fs.readFileSync(path.join(FIXTURES, name), "utf8");
    const page = pageOf(name);
    const types = page ? [TYPE_BY_PAGE[page]] : ["3t", "2tf", "tf"];
    for (const type of types) {
      assert.throws(() => parsePage(type, html), OddsParseError);
    }
  });
}

test("合成HTMLは締切前と締切時の両方の表記を含む", { skip: syntheticNames.length === 0 && "共通データなし" }, () => {
  const labels = syntheticNames.map((n) => extractTimeLabel(fs.readFileSync(path.join(FIXTURES, n), "utf8")));
  assert.ok(labels.some((l) => isFinalLabel(l)));
  assert.ok(labels.some((l) => l !== null && !isFinalLabel(l)));
});

// ---------------------------------------------------------------------
// 実物の HTML(git 管理外。あるときだけ)
// ---------------------------------------------------------------------

// 注意: 保存済みの解析JSON(*_T*.json.gz)は、取得した時点の Python の解析で作られている。
// 解析を後で直した場合(2連複の並びなど)と食い違うので、ここでは比べない。
// 現在の Python との一致は、共通の合成HTML(上)で確かめる。
function listRawHtml() {
  if (!fs.existsSync(RAW)) return [];
  const files = [];
  const walk = (dir) => {
    for (const ent of fs.readdirSync(dir, { withFileTypes: true })) {
      const p = path.join(dir, ent.name);
      if (ent.isDirectory()) walk(p);
      else if (/_(odds3t|odds2tf|oddstf)_T\w+\.html\.gz$/.test(ent.name)) files.push(p);
    }
  };
  walk(RAW);
  return files;
}

const rawFiles = listRawHtml();
const readRaw = (p) => zlib.gunzipSync(fs.readFileSync(p)).toString("utf8");

test("実物のHTMLがすべて解析できる", { skip: rawFiles.length === 0 && "data/raw/official_odds/ に実物が無い" }, () => {
  for (const p of rawFiles) {
    const got = parsePage(TYPE_BY_PAGE[pageOf(path.basename(p))], readRaw(p));
    assert.ok(got.label === null || typeof got.label === "string", p);
  }
});

const TODA = path.join(RAW, "2026/20261102");
test(
  "実物の検算: 2026-11-02 戸田1R 3連単 2-1-6=27.5、2連単 2-1=4.9(spec/07 2.2)",
  { skip: !fs.existsSync(path.join(TODA, "02-01_odds3t_TC.html.gz")) && "実物が無い" },
  () => {
    const t = parsePage("3t", readRaw(path.join(TODA, "02-01_odds3t_TC.html.gz")));
    assert.equal(t.odds.trifecta["2"]["1"]["6"], 27.5);
    assert.equal(t.label, "締切時オッズ");
    const e = parsePage("2tf", readRaw(path.join(TODA, "02-01_odds2tf_TC.html.gz")));
    assert.equal(e.odds.exacta["2"]["1"], 4.9);
  },
);

// ---------------------------------------------------------------------
// 小さな単体テスト(spec どおりの自前の最小データ)
// ---------------------------------------------------------------------

function cellsHtml(values, cls = "oddsPoint ") {
  return values.map((v) => `<td class="${cls}">${v}</td>`).join("\n");
}

test("toOdds: 数値・空欄・0.0・記号", () => {
  assert.equal(toOdds("27.5"), 27.5);
  assert.equal(toOdds(" 1,234.5 "), 1234.5);
  assert.equal(toOdds("0.0"), null); // 締切前の票なし
  assert.equal(toOdds(""), null);
  assert.equal(toOdds("-"), null);
  assert.equal(toOdds("欠場"), null);
  assert.equal(toOdds("1.0-1.0"), null);
});

test("toRange: 下限-上限", () => {
  assert.deepEqual(toRange("1.0-1.0"), { lower_limit: 1.0, upper_limit: 1.0 });
  assert.deepEqual(toRange("2.4-3.1"), { lower_limit: 2.4, upper_limit: 3.1 });
  assert.equal(toRange("1.0"), null);
  assert.equal(toRange("-"), null);
  assert.equal(toRange(""), null);
});

test("3連単の並び: 2-1-6 の位置(spec/07 2.2 の検算と同じ式)", () => {
  // セル i に i+1 を入れ、2-1-6 がどの位置かを式どおりに確かめる
  const cells = Array.from({ length: 120 }, (_, i) => String(i + 1));
  const t = parseTrifecta(cells).trifecta;
  // first=2 は列2(0始まりで1)、second=1 は others(2)=[1,3,4,5,6] の0番目 → b=0、
  // third=6 は others(2,1)=[3,4,5,6] の3番目 → r=3。idx = (b*4 + r)*6 + 1 = 19
  assert.equal(t["2"]["1"]["6"], 20);
  assert.equal(t["1"]["2"]["3"], 1);
  assert.equal(t["6"]["5"]["4"], 120);
  let n = 0;
  for (const a of Object.values(t)) for (const b of Object.values(a)) n += Object.keys(b).length;
  assert.equal(n, 120);
});

test("2連単・2連複の並び", () => {
  const cells = Array.from({ length: 45 }, (_, i) => String(i + 1));
  const { exacta, quinella } = parseExactaQuinella(cells);
  assert.equal(exacta["1"]["2"], 1);
  assert.equal(exacta["2"]["1"], 2);
  assert.equal(exacta["6"]["5"], 30);
  assert.equal(quinella["1"]["2"], 31);
  assert.equal(quinella["1"]["3"], 32);
  assert.equal(quinella["2"]["3"], 33);
  assert.equal(quinella["5"]["6"], 45);
  assert.equal(quinella["6"], undefined);
});

test("単勝・複勝", () => {
  const cells = ["1.5", "", "3.0", "-", "5.0", "0.0", "1.0-1.2", "", "1.1-2.0", "-", "3.0-4.0", "1.0-1.0"];
  const { win, place } = parseWinPlace(cells);
  assert.deepEqual(win, { 1: 1.5, 2: null, 3: 3.0, 4: null, 5: 5.0, 6: null });
  assert.equal(place["2"], null);
  assert.deepEqual(place["6"], { lower_limit: 1.0, upper_limit: 1.0 });
});

test("セル数が違えば OddsParseError(黙って間違った値を返さない)", () => {
  assert.throws(() => parseTrifecta(new Array(119).fill("1.0")), OddsParseError);
  assert.throws(() => parseExactaQuinella(new Array(46).fill("1.0")), OddsParseError);
  assert.throws(() => parseWinPlace([]), OddsParseError);
  assert.throws(() => parsePage("tf", "<html><body>メンテナンス中</body></html>"), OddsParseError);
});

test("extractCells: class の位置・引用符・入れ子のタグ・閉じ忘れ・文字参照", () => {
  const html = `
    <table><tr><th class="oddsPoint">見出し(th は数えない)</th>
    <td class="is-boatColor1">1</td>
    <td class="oddsPoint ">9.2</td>
    <td class='x oddsPoint'>&nbsp;13.0 </td>
    <td class=oddsPoint><span>51.0</span></td>
    <td class="oddsPointX">数えない</td>
    <td class="oddsPoint">87.6
    <td class="oddsPoint"></td>
    <!-- <td class="oddsPoint">コメントは数えない</td> -->
    <td class="oddsPoint">1.0-1.0</tr></table>`;
  assert.deepEqual(extractCells(html), ["9.2", "13.0", "51.0", "87.6", "", "1.0-1.0"]);
  assert.deepEqual(extractCells("<p>なし</p>"), []);
});

test("extractTimeLabel: 締切前と締切時、空白をまとめる、無ければ null", () => {
  assert.equal(
    extractTimeLabel('<div class="tab4_refresh"><p class="tab4_refreshText">\n  オッズ更新時間\n  17:07\n</p></div>'),
    "オッズ更新時間 17:07",
  );
  assert.equal(extractTimeLabel('<ul class="tab4_tabs"></ul><p class="tab4_time">締切時オッズ</p>'), "締切時オッズ");
  assert.equal(extractTimeLabel('<p class="tab4_refresh_refreshBtn">更新</p>'), null);
  assert.equal(extractTimeLabel("<p>なし</p>"), null);
  assert.equal(isFinalLabel("締切時オッズ"), true);
  assert.equal(isFinalLabel("オッズ更新時間 17:07"), false);
  assert.equal(isFinalLabel(null), false);
});
