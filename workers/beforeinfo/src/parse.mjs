// 公式の直前情報ページ(beforeinfo)の解析。Workers と Node の両方で動く純粋な関数。
//
// 収集担当の Python 実装 src/collect/beforeinfo.py の parse_beforeinfo を、同じ切り出し方・同じ正規表現で写したもの。
// どちらかを直したら、共通データ tests/fixtures/beforeinfo/ のテストを両方で必ず通すこと。
// 構造の説明は src/collect/BEFOREINFO_FORMAT.md と beforeinfo.py の先頭のコメント:
//   1. 選手の表: <table class="is-w748"> の <tbody> が枠番1〜6の順に6個。td の位置で読む
//      (0 枠番 / 2 名前(toban=登録番号) / 3 体重 / 4 展示タイム / 5 チルト / 6 プロペラ / 7 部品交換 / 12 調整重量)
//   2. スタート展示: <table class="is-w238"> の tbody の <tr> が進入コース1〜6
//   3. 気象: <div class="weather1"> 〜 class="weather1_stand"
// CPU 時間の節約のため、DOM パーサーは使わず indexOf と正規表現だけで読む。

export const BOATS = [1, 2, 3, 4, 5, 6];
// 部品交換の名前 → Open API の parts.number(Python の PART_NUMBERS と同じ)
export const PART_NUMBERS = { ピストン: 1, リング: 2, 電気: 3, キャブ: 4, シリンダ: 5, シャフト: 6, ギヤ: 7, キャリボ: 8 };

export class BeforeinfoParseError extends Error {
  constructor(message) {
    super(message);
    this.name = "BeforeinfoParseError";
  }
}

// ---------------------------------------------------------------------
// 文字列の小道具(Python の _slice / _text / _number / _int_or_none と同じ)
// ---------------------------------------------------------------------

/** startMarker から、その後の最初の endMarker までを返す。無ければ null。 */
export function slice(html, startMarker, endMarker) {
  const i = html.indexOf(startMarker);
  if (i < 0) return null;
  const j = html.indexOf(endMarker, i);
  return j < 0 ? html.slice(i) : html.slice(i, j + endMarker.length);
}

/** タグを除き、&nbsp; と空白を詰めた文字列。 */
export function textOf(fragment) {
  const s = fragment
    .replace(/<[^>]*>/g, "")
    .split("&nbsp;").join(" ")
    .split("&amp;").join("&")
    .split("　").join(" ");
  return s.replace(/\s+/g, " ").trim();
}

/** '51.5kg' '-0.5' '22.0℃' '4m' '5cm' → 数値。無ければ null。 */
export function toNumber(text) {
  const m = /-?(?:\d+(?:\.\d+)?|\.\d+)/.exec(text || "");
  return m ? Number(m[0]) : null;
}

/** Python の int(round(v))(偶数への丸め)。 */
export function intOrNull(v) {
  if (v === null) return null;
  const f = Math.floor(v);
  const diff = v - f;
  if (diff > 0.5) return f + 1;
  if (diff < 0.5) return f;
  return f % 2 === 0 ? f : f + 1;
}

/** '.03' → [0.03, false]、'F.03' → [-0.03, true]、'' → [null, false]。 */
export function parseStartTiming(text) {
  const t = (text || "").trim().toUpperCase();
  if (!t) return [null, false];
  const flying = t.startsWith("F");
  const v = toNumber(t.replace(/^[FL]+/, ""));
  if (v === null) return [null, flying];
  return [flying ? -Math.abs(v) : v, flying];
}

const FULLWIDTH_DIGITS = /[０-９]/g;

/** 部品交換の <li> を Open API と同じ形 [{number_source, number, quantity}] にする。 */
export function parseParts(fragment) {
  const parts = [];
  for (const m of fragment.matchAll(/<li\b[^>]*>([\s\S]*?)<\/li>/g)) {
    const t = textOf(m[1]).replace(FULLWIDTH_DIGITS, (c) => String(c.charCodeAt(0) - 0xff10));
    if (!t) continue;
    const mm = /^([\s\S]*?)\s*(?:[×xX✕*]\s*(\d+))?$/.exec(t);
    const name = mm[1].trim();
    parts.push({
      number_source: name,
      number: Object.prototype.hasOwnProperty.call(PART_NUMBERS, name) ? PART_NUMBERS[name] : null,
      quantity: mm[2] ? Number(mm[2]) : null,
    });
  }
  return parts;
}

function findAll(re, text) {
  return [...text.matchAll(re)].map((m) => m[1]);
}

// ---------------------------------------------------------------------
// 解析の本体
// ---------------------------------------------------------------------

function parseRacerTable(html) {
  const table = slice(html, '<table class="is-w748"', "</table>");
  if (table === null) throw new BeforeinfoParseError("選手の表(is-w748)が見つからない");
  const bodies = findAll(/<tbody\b[^>]*>([\s\S]*?)<\/tbody>/g, table);
  if (bodies.length !== 6) throw new BeforeinfoParseError(`選手の表の tbody が6個ではない: ${bodies.length}`);
  const boats = {};
  for (const body of bodies) {
    const tds = findAll(/<td\b[^>]*>([\s\S]*?)<\/td>/g, body);
    if (tds.length < 13) throw new BeforeinfoParseError(`選手の表の td が足りない: ${tds.length}`);
    const boat = intOrNull(toNumber(textOf(tds[0])));
    if (!BOATS.includes(boat)) throw new BeforeinfoParseError(`枠番が読めない: ${textOf(tds[0])}`);
    const toban = /toban=(\d+)/.exec(tds[2]);
    boats[String(boat)] = {
      racer_number: toban ? Number(toban[1]) : null,
      exhibition_time: toNumber(textOf(tds[4])),
      tilt_adjustment: toNumber(textOf(tds[5])),
      weight: toNumber(textOf(tds[3])),
      weight_adjustment: toNumber(textOf(tds[12])),
      propeller_new: textOf(tds[6]).includes("新"),
      parts: parseParts(tds[7]),
      start_course: null,
      start_timing: null,
      start_flying: false,
      absent: false,
    };
  }
  return boats;
}

function parseStartTable(html) {
  const table = slice(html, '<table class="is-w238"', "</table>");
  if (table === null) throw new BeforeinfoParseError("スタート展示の表(is-w238)が見つからない");
  const tbody = slice(table, "<tbody", "</tbody>") || "";
  const rows = findAll(/<tr\b[^>]*>([\s\S]*?)<\/tr>/g, tbody);
  const out = [];
  rows.forEach((row, i) => {
    const num = /class="table1_boatImage1Number[^"]*">([\s\S]*?)<\/span>/.exec(row);
    if (!num) return;
    const st = /class="table1_boatImage1Time[^"]*">([\s\S]*?)<\/span>/.exec(row);
    const boat = intOrNull(toNumber(textOf(num[1])));
    if (BOATS.includes(boat)) out.push([i + 1, boat, st ? textOf(st[1]) : ""]);
  });
  return out;
}

export function parseWeather(html) {
  const block = slice(html, '<div class="weather1"', 'class="weather1_stand"') || "";
  const weather = /is-weather(\d+)/.exec(block);
  const windDir = /is-wind(\d+)/.exec(block);
  const pairs = {};
  const re =
    /weather1_bodyUnitLabelTitle">([^<]*)<\/span>\s*<span class="weather1_bodyUnitLabelData">([^<]*)<\/span>/g;
  for (const m of block.matchAll(re)) pairs[textOf(m[1])] = textOf(m[2]);
  const title = /class="weather1_title">([\s\S]*?)<\/p>/.exec(block);
  return {
    weather_number: weather ? Number(weather[1]) : null,
    wind_direction_number: windDir ? Number(windDir[1]) : null,
    wind_speed: intOrNull(toNumber(pairs["風速"] ?? "")),
    wave_height: intOrNull(toNumber(pairs["波高"] ?? "")),
    air_temperature: toNumber(pairs["気温"] ?? ""),
    water_temperature: toNumber(pairs["水温"] ?? ""),
    as_of: title ? textOf(title[1]).split("水面気象情報").join("").trim() : null,
  };
}

/**
 * 直前情報ページを解析して {status, weather, boats} を返す(Python の parse_beforeinfo と同じ)。
 * status: not_posted(展示タイムもスタート展示も1艇も無い)/ partial(出走艇の一部が欠ける)/ ok
 * 欠場(absent): 体重とチルトがどちらも空欄で、スタート展示にもいない艇(他の艇には載っている)。
 */
export function parseBeforeinfo(html) {
  const boats = parseRacerTable(html);
  for (const [course, boat, stText] of parseStartTable(html)) {
    const b = boats[String(boat)];
    b.start_course = course;
    [b.start_timing, b.start_flying] = parseStartTiming(stText);
  }
  const weather = parseWeather(html);
  const values = Object.values(boats);
  const someoneHasWeight = values.some((b) => b.weight !== null || b.tilt_adjustment !== null);
  for (const b of values) {
    if (someoneHasWeight && b.weight === null && b.tilt_adjustment === null && b.start_course === null) b.absent = true;
  }
  const entrants = values.filter((b) => !b.absent);
  const hasAny = entrants.some((b) => b.exhibition_time !== null || b.start_course !== null);
  const complete = entrants.every((b) => b.exhibition_time !== null && b.start_course !== null);
  const status = entrants.length > 0 && complete ? "ok" : hasAny ? "partial" : "not_posted";
  return { status, weather, boats };
}
