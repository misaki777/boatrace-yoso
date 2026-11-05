// 日付・締切の判定、入力チェック、キャッシュキー、上限の判定などの純粋な関数(spec/07 9.2〜9.3、9.8)。

import { PAGE_BY_TYPE } from "./parse.mjs";

export const ALL_TYPES = ["3t", "2tf", "tf"]; // 取得する順番もこの順
export const CACHE_TTL_SEC = 60; // KV の expirationTtl の最小値
export const DEFAULT_DAILY_LIMIT = 300;
export const MAX_DAILY_LIMIT = 700; // KV の書き込み上限(1,000/日)を超えないための上限(spec/07 Q8)
export const PAGE_INTERVAL_MS = 1000; // ページの間は応答の終わりから1秒以上空ける
export const UPSTREAM_TIMEOUT_MS = 13000; // 1ページの待ち時間の上限(3ページ+間隔でスマホ側の45秒に収まる)
export const STOPPED_TTL_SEC = 2 * 24 * 3600; // 停止フラグは日付つきのキーなので、2日で自然に消す
export const COUNT_TTL_SEC = 2 * 24 * 3600;
export const USER_AGENT = "boatrace-yoso-proxy/0.1 (personal use)";
export const OFFICIAL_BASE = "https://www.boatrace.jp/owpc/pc/race/";

const JST_OFFSET_MS = 9 * 3600 * 1000;

// ---------------------------------------------------------------------
// 日付と時刻(JST)
// ---------------------------------------------------------------------

function pad(n, width = 2) {
  return String(n).padStart(width, "0");
}

/** エポックミリ秒 → JST の 'YYYYMMDD'。 */
export function jstDateString(ms) {
  const d = new Date(ms + JST_OFFSET_MS);
  return `${d.getUTCFullYear()}${pad(d.getUTCMonth() + 1)}${pad(d.getUTCDate())}`;
}

/** エポックミリ秒 → '2026-11-03T10:40:12+09:00'(秒まで)。 */
export function formatJstIso(ms) {
  const d = new Date(ms + JST_OFFSET_MS);
  return (
    `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}` +
    `T${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())}+09:00`
  );
}

const CLOSED_AT_RE = /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2}))?(?:\.\d+)?(Z|[+-]\d{2}:\d{2})?$/;

/**
 * 締切時刻の文字列 → エポックミリ秒。時差の表記が無ければ JST とみなす。形が違えば null。
 * 例: '2026-11-03T10:45:00+09:00'(公開JSONの closed_at)、'2026-11-03 10:45:00'(openapi)。
 */
export function parseClosedAt(text) {
  if (typeof text !== "string") return null;
  const m = CLOSED_AT_RE.exec(text.trim());
  if (!m) return null;
  const [, y, mo, d, h, mi, s, tz] = m;
  const nums = [y, mo, d, h, mi, s ?? "0"].map(Number);
  if (nums[1] < 1 || nums[1] > 12 || nums[2] < 1 || nums[2] > 31 || nums[3] > 23 || nums[4] > 59 || nums[5] > 59) {
    return null;
  }
  let offsetMs = JST_OFFSET_MS;
  if (tz === "Z") offsetMs = 0;
  else if (tz) {
    const sign = tz[0] === "-" ? -1 : 1;
    offsetMs = sign * (Number(tz.slice(1, 3)) * 3600 + Number(tz.slice(4, 6)) * 60) * 1000;
  }
  const utc = Date.UTC(nums[0], nums[1] - 1, nums[2], nums[3], nums[4], nums[5]);
  // 2月30日のような存在しない日付を弾く
  const check = new Date(utc);
  if (check.getUTCDate() !== nums[2] || check.getUTCMonth() !== nums[1] - 1) return null;
  return utc - offsetMs;
}

// ---------------------------------------------------------------------
// 入力チェック(spec/07 9.2)
// ---------------------------------------------------------------------

/**
 * クエリを検証する。searchParams は URLSearchParams(get を持つもの)。
 * 戻り値: {ok: true, value: {date, jcd, rno, types, closedAtMs, raceId}} か {ok: false, message}
 */
export function validateQuery(searchParams, nowMs) {
  const get = (k) => {
    const v = searchParams.get(k);
    return v === null ? null : String(v);
  };
  const date = get("date");
  if (date === null || !/^\d{8}$/.test(date)) return fail("date は YYYYMMDD の形で指定してください");
  if (date !== jstDateString(nowMs)) return fail("date は今日(日本時間)の日付だけ受け付けます");

  const jcd = get("jcd");
  if (jcd === null || !/^\d{2}$/.test(jcd) || Number(jcd) < 1 || Number(jcd) > 24) {
    return fail("jcd は 01〜24 の2桁で指定してください");
  }
  const rnoText = get("rno");
  if (rnoText === null || !/^\d{1,2}$/.test(rnoText) || Number(rnoText) < 1 || Number(rnoText) > 12) {
    return fail("rno は 1〜12 で指定してください");
  }
  const rno = Number(rnoText);

  const typesText = get("types");
  let types;
  if (typesText === null || typesText === "") {
    types = ALL_TYPES.slice();
  } else {
    const items = typesText.split(",").map((s) => s.trim());
    if (items.some((s) => !ALL_TYPES.includes(s))) {
      return fail("types は 3t・2tf・tf をカンマ区切りで指定してください");
    }
    types = ALL_TYPES.filter((t) => items.includes(t)); // 重複を除き、取得の順番にそろえる
  }

  const closedAtMs = parseClosedAt(get("closed_at"));
  if (closedAtMs === null) return fail("closed_at は 2026-11-03T10:45:00+09:00 の形で指定してください");
  if (jstDateString(closedAtMs) !== date) return fail("closed_at の日付が date と違います");

  return { ok: true, value: { date, jcd, rno, types, closedAtMs, raceId: makeRaceId(date, jcd, rno) } };
}

function fail(message) {
  return { ok: false, message };
}

export function makeRaceId(date, jcd, rno) {
  return `${date}-${jcd}-${pad(rno)}`;
}

/** 締切を過ぎているか(締切ちょうども過ぎたとみなす)。 */
export function isClosed(closedAtMs, nowMs) {
  return nowMs >= closedAtMs;
}

export function officialUrl(type, date, jcd, rno) {
  return `${OFFICIAL_BASE}${PAGE_BY_TYPE[type]}?rno=${rno}&jcd=${jcd}&hd=${date}`;
}

// ---------------------------------------------------------------------
// KV のキーと上限
// ---------------------------------------------------------------------

export function cacheKey(raceId, type) {
  return `cache:${raceId}:${type}`;
}

export function countKey(date) {
  return `count:${date}`;
}

export function stoppedKey(date) {
  return `stopped:${date}`;
}

/** 環境変数 DAILY_LIMIT → 1日の上限。不正なら既定値、大きすぎれば MAX_DAILY_LIMIT に切り詰める。 */
export function parseDailyLimit(text) {
  if (text === undefined || text === null || String(text).trim() === "") return DEFAULT_DAILY_LIMIT;
  const s = String(text).trim();
  if (!/^\d+$/.test(s) || Number(s) < 1) return DEFAULT_DAILY_LIMIT;
  return Math.min(Number(s), MAX_DAILY_LIMIT);
}

/** KV に入っている使用数の文字列 → 数。読めなければ 0。 */
export function parseCount(text) {
  const n = Number(text);
  return Number.isInteger(n) && n >= 0 ? n : 0;
}

/** 今日の使用数 used に needed ページを足しても上限以内か。 */
export function withinDailyLimit(used, needed, limit) {
  return used + needed <= limit;
}

/** キャッシュの値がまだ使えるか(KV の TTL は端のキャッシュで延びることがあるので、時刻でも確かめる)。 */
export function isCacheFresh(entry, nowMs) {
  return (
    !!entry &&
    typeof entry.fetched_at_ms === "number" &&
    nowMs - entry.fetched_at_ms >= 0 &&
    nowMs - entry.fetched_at_ms < CACHE_TTL_SEC * 1000 &&
    !!entry.odds
  );
}

/** 前回の応答の終わりから PAGE_INTERVAL_MS 空けるために、あと何ミリ秒待つか。 */
export function waitBeforeNext(lastEndMs, nowMs) {
  if (lastEndMs === null) return 0;
  return Math.max(0, PAGE_INTERVAL_MS - (nowMs - lastEndMs));
}

// ---------------------------------------------------------------------
// 認証と CORS(spec/07 9.8)
// ---------------------------------------------------------------------

/**
 * 時間が一定になる文字列の比較。長さが違っても、長い方の長さだけ必ず回す。
 * (Workers の crypto.subtle.timingSafeEqual は Node 16 に無いので、自前で書く)
 */
export function timingSafeEqual(a, b) {
  const enc = new TextEncoder();
  const x = enc.encode(String(a));
  const y = enc.encode(String(b));
  const len = Math.max(x.length, y.length);
  let diff = x.length ^ y.length;
  for (let i = 0; i < len; i++) {
    diff |= (x[i % (x.length || 1)] ?? 0) ^ (y[i % (y.length || 1)] ?? 0);
  }
  return diff === 0;
}

/** 環境変数 ALLOWED_ORIGINS(カンマ区切り)→ 許可するオリジンの配列。末尾の "/" は除く。 */
export function parseAllowedOrigins(text) {
  if (!text) return [];
  return String(text)
    .split(",")
    .map((s) => s.trim().replace(/\/+$/, ""))
    .filter(Boolean);
}

export function isOriginAllowed(origin, allowed) {
  return typeof origin === "string" && allowed.includes(origin);
}
