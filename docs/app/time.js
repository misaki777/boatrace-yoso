// 時刻の扱い。表示はすべて JST(端末のタイムゾーンに関係なく +9時間で計算する)。
// デバッグ用に URL の ?now=2026-11-02T15:05 で「現在時刻」を固定できる(タイムゾーン無しは JST とみなす)。

const JST_OFFSET_MS = 9 * 60 * 60 * 1000;
const WEEKDAYS = ['日', '月', '火', '水', '木', '金', '土'];

let fixedNowMs = null;

/** URL パラメータ now を読み、固定の現在時刻を設定する。不正な値は無視する。 */
export function initClock(search) {
  const value = new URLSearchParams(search).get('now');
  if (!value) return;
  const ms = parseNowParam(value);
  if (ms !== null) fixedNowMs = ms;
  else console.warn('now パラメータを解釈できません:', value);
}

/** ?now= の値をミリ秒に変換する。タイムゾーンの指定が無ければ JST とみなす */
export function parseNowParam(value) {
  let text = value.trim();
  if (/^\d{4}-\d{2}-\d{2}$/.test(text)) text += 'T00:00';
  if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(text)) text += ':00';
  if (!/(Z|[+-]\d{2}:?\d{2})$/.test(text)) text += '+09:00';
  const ms = Date.parse(text);
  return Number.isNaN(ms) ? null : ms;
}

export function isClockFixed() {
  return fixedNowMs !== null;
}

/** 現在時刻(ミリ秒) */
export function nowMs() {
  return fixedNowMs !== null ? fixedNowMs : Date.now();
}

/** ISO 8601 文字列(+09:00 付き)をミリ秒にする。読めなければ NaN */
export function parseIso(iso) {
  return iso ? Date.parse(iso) : NaN;
}

/** ミリ秒 → JST の年月日・時分・曜日 */
export function jstParts(ms) {
  const d = new Date(ms + JST_OFFSET_MS);
  return {
    year: d.getUTCFullYear(),
    month: d.getUTCMonth() + 1,
    day: d.getUTCDate(),
    hour: d.getUTCHours(),
    minute: d.getUTCMinutes(),
    weekday: d.getUTCDay(),
  };
}

const pad2 = (n) => String(n).padStart(2, '0');

/** JST の日付 'YYYY-MM-DD' */
export function jstDateString(ms) {
  const p = jstParts(ms);
  return `${p.year}-${pad2(p.month)}-${pad2(p.day)}`;
}

/** 今日(JST)の 'YYYY-MM-DD' */
export function todayJst() {
  return jstDateString(nowMs());
}

/** ISO → 'HH:MM'(JST) */
export function fmtClock(iso) {
  const ms = parseIso(iso);
  if (Number.isNaN(ms)) return '--:--';
  const p = jstParts(ms);
  return `${pad2(p.hour)}:${pad2(p.minute)}`;
}

/** 'YYYY-MM-DD' の曜日(日〜土) */
export function weekdayOf(dateStr) {
  const [y, m, d] = dateStr.split('-').map(Number);
  return WEEKDAYS[new Date(Date.UTC(y, m - 1, d)).getUTCDay()];
}

/** 'YYYY-MM-DD' → '11/2' */
export function fmtMonthDay(dateStr) {
  const [, m, d] = dateStr.split('-').map(Number);
  return `${m}/${d}`;
}

/** 'YYYY-MM-DD' → '11/2(月)' */
export function fmtMonthDayWeek(dateStr) {
  return `${fmtMonthDay(dateStr)}(${weekdayOf(dateStr)})`;
}

/**
 * データ更新時刻の表示。基準の日付(表示中の日)と同じ日なら 'HH:MM'、違えば 'M/D HH:MM'。
 */
export function fmtUpdated(iso, baseDateStr) {
  const ms = parseIso(iso);
  if (Number.isNaN(ms)) return '—';
  const dateStr = jstDateString(ms);
  const clock = fmtClock(iso);
  return dateStr === baseDateStr ? clock : `${fmtMonthDay(dateStr)} ${clock}`;
}

/** ISO → 'YYYY-MM-DD HH:MM'(JST) */
export function fmtDateTime(iso) {
  const ms = parseIso(iso);
  if (Number.isNaN(ms)) return '—';
  return `${jstDateString(ms)} ${fmtClock(iso)}`;
}

/** 締切までの残り分(切り上げ)。締切を過ぎていれば 0 以下 */
export function minutesUntil(iso) {
  const ms = parseIso(iso);
  if (Number.isNaN(ms)) return NaN;
  return Math.ceil((ms - nowMs()) / 60000);
}

/** 'YYYYMMDD' ⇔ 'YYYY-MM-DD' */
export function compactToDate(compact) {
  return `${compact.slice(0, 4)}-${compact.slice(4, 6)}-${compact.slice(6, 8)}`;
}
