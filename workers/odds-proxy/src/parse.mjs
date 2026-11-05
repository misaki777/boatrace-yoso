// 公式サイトのオッズページの HTML を解析する純粋な関数(Workers と Node の両方で動く)。
//
// Python 側(src/collect/official_odds.py の extract_cells / parse_*)と同じ結果になるように書いている。
// どちらかを直したら、両方のテスト(tests/fixtures/official_odds/ の共通データ)を必ず通すこと。
//
// CPU 時間を節約するため、DOM パーサーは使わない:
//   1. oddsPoint が最初に現れる位置から、最後に現れたセルの </td> までを indexOf で切り出す
//      (ヘッダ・メニュー・出走表などは解析しない)
//   2. 切り出した範囲だけを、タグの正規表現で順に読む(Python の HTMLParser と同じ状態遷移)
//   3. オッズ時点の表示(p.tab4_refreshText / p.tab4_time)は indexOf で探してその要素だけ読む

export const BOATS = [1, 2, 3, 4, 5, 6];

// 券種の指定(API の types)と、公式サイトのページ名・セル数の対応
export const PAGE_BY_TYPE = { "3t": "odds3t", "2tf": "odds2tf", tf: "oddstf" };
export const CELL_COUNT_BY_TYPE = { "3t": 120, "2tf": 45, tf: 12 };

/** HTML の構造が想定と違う(セル数が違うなど)。間違った値を返さないために投げる。 */
export class OddsParseError extends Error {
  constructor(message) {
    super(message);
    this.name = "OddsParseError";
  }
}

// ---------------------------------------------------------------------
// 文字列の補助
// ---------------------------------------------------------------------

const NAMED_ENTITIES = { amp: "&", lt: "<", gt: ">", quot: '"', apos: "'", nbsp: " " };

/** よく使う文字参照だけを戻す(Python の convert_charrefs に相当。オッズのセルで使われる範囲)。 */
export function decodeEntities(text) {
  if (text.indexOf("&") === -1) return text;
  return text.replace(/&(#[0-9]+|#[xX][0-9a-fA-F]+|[a-zA-Z]+);/g, (whole, body) => {
    if (body[0] === "#") {
      const code = body[1] === "x" || body[1] === "X" ? parseInt(body.slice(2), 16) : parseInt(body.slice(1), 10);
      try {
        return String.fromCodePoint(code);
      } catch (_e) {
        return whole;
      }
    }
    const ch = NAMED_ENTITIES[body.toLowerCase()];
    return ch === undefined ? whole : ch;
  });
}

/** 開始タグの属性文字列から class の値を取り出す(無ければ "")。引用符なしの値にも対応する。 */
export function classOf(attrText) {
  const m = /(?:^|\s)class\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+))/i.exec(attrText);
  if (!m) return "";
  return decodeEntities(m[1] ?? m[2] ?? m[3] ?? "");
}

function hasClass(attrText, name) {
  return classOf(attrText).split(/\s+/).includes(name);
}

// タグ(開始・終了)とコメント。グループ1: "/" か "", 2: タグ名, 3: 属性
const TAG_RE = /<!--[\s\S]*?-->|<(\/?)([a-zA-Z][^\s/>]*)([^>]*)>/g;

// ---------------------------------------------------------------------
// セルとオッズ時点の抽出
// ---------------------------------------------------------------------

/**
 * class に oddsPoint を含む td の文字列を、文書の順に集める。
 * Python の _OddsHTMLParser と同じく、td・tr・th の開始タグが来たら閉じ忘れのセルを閉じる。
 */
export function extractCells(html) {
  const first = html.indexOf("oddsPoint");
  if (first === -1) return [];
  const last = html.lastIndexOf("oddsPoint");
  // 範囲の始まり: 最初の oddsPoint を含むタグの "<"。終わり: 最後の oddsPoint の後の最初の </td>
  const start = html.lastIndexOf("<", first);
  const endTd = html.indexOf("</td>", last);
  const region = html.slice(start === -1 ? 0 : start, endTd === -1 ? html.length : endTd + 5);

  const cells = [];
  let cell = null; // 開いているセルの文字列の断片
  let pos = 0;
  TAG_RE.lastIndex = 0;
  let m;
  while ((m = TAG_RE.exec(region)) !== null) {
    if (cell !== null && m.index > pos) cell.push(region.slice(pos, m.index));
    pos = TAG_RE.lastIndex;
    if (m[2] === undefined) continue; // コメント
    const tag = m[2].toLowerCase();
    if (m[1] === "/") {
      if (tag === "td" && cell !== null) {
        cells.push(cellText(cell));
        cell = null;
      }
      continue;
    }
    if ((tag === "td" || tag === "tr" || tag === "th") && cell !== null) {
      cells.push(cellText(cell));
      cell = null;
    }
    if (tag === "td" && hasClass(m[3], "oddsPoint")) cell = [];
  }
  if (cell !== null) {
    if (pos < region.length) cell.push(region.slice(pos));
    cells.push(cellText(cell));
  }
  return cells;
}

function cellText(parts) {
  return decodeEntities(parts.join("")).trim();
}

/**
 * オッズ時点の表示を返す。締切前は p.tab4_refreshText(「オッズ更新時間 17:07」)、
 * 締切後は p.tab4_time(「締切時オッズ」)。文書の最初のものを使い、空白はまとめて1つにする。
 * 見つからなければ null(Python の extract_cells の2番目の戻り値と同じ)。
 */
export function extractTimeLabel(html) {
  let from = 0;
  for (;;) {
    const a = html.indexOf("tab4_time", from);
    const b = html.indexOf("tab4_refreshText", from);
    const idx = a === -1 ? b : b === -1 ? a : Math.min(a, b);
    if (idx === -1) return null;
    const tagStart = html.lastIndexOf("<", idx);
    const tagEnd = html.indexOf(">", idx);
    if (tagStart !== -1 && tagEnd !== -1) {
      const m = /^<([a-zA-Z][^\s/>]*)([^>]*)>$/.exec(html.slice(tagStart, tagEnd + 1));
      if (m && m[1].toLowerCase() === "p" && (hasClass(m[2], "tab4_time") || hasClass(m[2], "tab4_refreshText"))) {
        return readParagraph(html, tagEnd + 1);
      }
    }
    from = idx + 1;
  }
}

/** p の開始タグの直後から、対応する </p> までの文字列(入れ子の p も数える)。閉じていなければ null。 */
function readParagraph(html, from) {
  const re = new RegExp(TAG_RE.source, "g");
  re.lastIndex = from;
  const parts = [];
  let pos = from;
  let depth = 1;
  let m;
  while ((m = re.exec(html)) !== null) {
    if (m.index > pos) parts.push(html.slice(pos, m.index));
    pos = re.lastIndex;
    if (m[2] === undefined || m[2].toLowerCase() !== "p") continue;
    depth += m[1] === "/" ? -1 : 1;
    if (depth <= 0) {
      return decodeEntities(parts.join("")).split(/\s+/).filter(Boolean).join(" ");
    }
  }
  return null;
}

/** 「締切時」を含む表示なら締切時オッズ。 */
export function isFinalLabel(label) {
  return typeof label === "string" && label.includes("締切時");
}

// ---------------------------------------------------------------------
// 数値への変換
// ---------------------------------------------------------------------

const NUMBER_RE = /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/;

/** '27.5' → 27.5。数値でないもの(空欄・'-'・'欠場' など)と 0 以下は null(締切前の票なしは '0.0')。 */
export function toOdds(text) {
  const t = String(text).replace(/,/g, "").trim();
  if (!NUMBER_RE.test(t)) return null;
  const value = Number(t);
  return Number.isFinite(value) && value > 0 ? value : null;
}

/** '1.0-1.0' → {lower_limit: 1.0, upper_limit: 1.0}。形が違えば null。 */
export function toRange(text) {
  const parts = String(text).replace(/,/g, "").trim().split("-");
  if (parts.length !== 2) return null;
  const lower = toOdds(parts[0]);
  const upper = toOdds(parts[1]);
  if (lower === null || upper === null) return null;
  return { lower_limit: lower, upper_limit: upper };
}

// ---------------------------------------------------------------------
// 券種ごとの並び(spec/07 2.2)
// ---------------------------------------------------------------------

function others(...exclude) {
  return BOATS.filter((b) => !exclude.includes(b));
}

function checkCount(type, cells) {
  const expected = CELL_COUNT_BY_TYPE[type];
  if (cells.length !== expected) {
    throw new OddsParseError(`${PAGE_BY_TYPE[type]}: oddsPoint のセル数が ${cells.length} です(想定 ${expected})`);
  }
}

/** 3連単120セル。列=1着、行のブロック=2着、ブロック内の行=3着。 */
export function parseTrifecta(cells) {
  checkCount("3t", cells);
  const out = {};
  for (const b of BOATS) out[String(b)] = {};
  let idx = 0;
  for (let b = 0; b < 5; b++) {
    for (let r = 0; r < 4; r++) {
      for (const first of BOATS) {
        const second = others(first)[b];
        const third = others(first, second)[r];
        const row = (out[String(first)][String(second)] ??= {});
        row[String(third)] = toOdds(cells[idx]);
        idx++;
      }
    }
  }
  return { trifecta: out };
}

/** 前半30セルが2連単、後半15セルが2連複(1-2, 1-3, 2-3, 1-4, 2-4, 3-4, … の順)。 */
export function parseExactaQuinella(cells) {
  checkCount("2tf", cells);
  const exacta = {};
  for (const b of BOATS) exacta[String(b)] = {};
  let idx = 0;
  for (let r = 0; r < 5; r++) {
    for (const first of BOATS) {
      const second = others(first)[r];
      exacta[String(first)][String(second)] = toOdds(cells[idx]);
      idx++;
    }
  }
  const quinella = {};
  for (const b of BOATS.slice(0, -1)) quinella[String(b)] = {};
  for (const second of BOATS.slice(1)) {
    for (let first = 1; first < second; first++) {
      quinella[String(first)][String(second)] = toOdds(cells[idx]);
      idx++;
    }
  }
  return { exacta, quinella };
}

/** 前半6セルが単勝、後半6セルが複勝(下限-上限)。どちらも1〜6号艇の順。 */
export function parseWinPlace(cells) {
  checkCount("tf", cells);
  const win = {};
  const place = {};
  BOATS.forEach((b, i) => {
    win[String(b)] = toOdds(cells[i]);
    place[String(b)] = toRange(cells[6 + i]);
  });
  return { win, place };
}

const PARSERS = { "3t": parseTrifecta, "2tf": parseExactaQuinella, tf: parseWinPlace };

/**
 * 1ページ分の HTML を解析して {odds, label} を返す。type は "3t" / "2tf" / "tf"。
 * 構造が違えば OddsParseError を投げる。
 */
export function parsePage(type, html) {
  const parser = PARSERS[type];
  if (!parser) throw new Error(`未知の券種です: ${type}`);
  const odds = parser(extractCells(html));
  return { odds, label: extractTimeLabel(html) };
}
