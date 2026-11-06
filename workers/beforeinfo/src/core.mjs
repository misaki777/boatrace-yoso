// 時刻・窓の判定・上限・R2 のキーなどの純粋な関数(spec/12 1.2、2.1)。

export const FIRST_WINDOW = [10, 15]; // 締切の15〜10分前(両端を含む)
export const LAST_CHANCE_MINUTES = 5; // 取り損ねたレース(1回の起動の3ページ上限で回されたもの)はここまで取る
export const MAX_PAGES_PER_TICK = 3; // 1回の起動で取得・解析するのは最大3ページ(CPU 10ms のため)
export const PAGE_INTERVAL_MS = 1500; // 応答の終わりから1.5秒以上空ける
export const UPSTREAM_TIMEOUT_MS = 20000;
export const DEFAULT_DAILY_LIMIT = 300;
export const MAX_CONSECUTIVE_FAILURES = 3;
export const DISPATCH_MIN_INTERVAL_MS = 4 * 60 * 1000; // 前回の起動から4分未満なら送らない
export const USER_AGENT = "boatrace-yoso-beforeinfo/0.1 (personal use)";

const JST_MS = 9 * 3600 * 1000;
const pad = (n) => String(n).padStart(2, "0");

export function jstDateString(ms) {
  const d = new Date(ms + JST_MS);
  return `${d.getUTCFullYear()}${pad(d.getUTCMonth() + 1)}${pad(d.getUTCDate())}`;
}

export function formatJstIso(ms) {
  const d = new Date(ms + JST_MS);
  return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}T${pad(d.getUTCHours())}:${pad(
    d.getUTCMinutes(),
  )}:${pad(d.getUTCSeconds())}+09:00`;
}

/** '2026-11-06T10:45:00+09:00'(時差なしは JST)→ ミリ秒。不正なら null。 */
export function parseClosedAt(text) {
  if (typeof text !== "string") return null;
  const s = /([Zz]|[+-]\d{2}:\d{2})$/.test(text) ? text : `${text.replace(" ", "T")}+09:00`;
  const ms = Date.parse(s);
  return Number.isFinite(ms) ? ms : null;
}

export function officialUrl(jcd, rno, date) {
  return `https://www.boatrace.jp/owpc/pc/race/beforeinfo?rno=${Number(rno)}&jcd=${pad(Number(jcd))}&hd=${date}`;
}

// R2 のキー
export const scheduleKey = (date) => `schedule/${date}.json`;
export const stateKey = (date) => `beforeinfo/${date}/_state.json`;
export const stoppedKey = (date) => `beforeinfo/${date}/_stopped.json`;
export function resultKey(date, jcd, rno) {
  return `beforeinfo/${date}/${pad(Number(jcd))}-${pad(Number(rno))}.json`;
}

export function parseDailyLimit(text) {
  const s = String(text ?? "").trim();
  return /^\d+$/.test(s) && Number(s) > 0 ? Number(s) : DEFAULT_DAILY_LIMIT;
}

/**
 * 取得の時間帯(締切の何分前か)を環境変数から読む。不正なら既定値(15〜10分前、取り損ねは5分前まで)。
 * 稼働後の実績(展示が載る時刻)を見て変えられるようにしている(C-8 の確認: 締切21分前ではまだ未掲載だった)。
 */
export function parseWindow(env = {}) {
  const num = (v, d) => {
    const n = Number(v);
    return v !== undefined && v !== null && String(v).trim() !== "" && Number.isFinite(n) ? n : d;
  };
  const start = num(env.WINDOW_START_MIN, FIRST_WINDOW[1]);
  const end = num(env.WINDOW_END_MIN, FIRST_WINDOW[0]);
  const last = num(env.LAST_CHANCE_MIN, LAST_CHANCE_MINUTES);
  // 4分前より後は取らない(予想の確定が間に合わない)。順序が崩れていれば既定に戻す
  if (!(start >= end && end >= last && last >= LAST_CHANCE_MINUTES && start <= 60)) {
    return { start: FIRST_WINDOW[1], end: FIRST_WINDOW[0], last: LAST_CHANCE_MINUTES };
  }
  return { start, end, last };
}

/**
 * このレースを今取るか(利用者の決定 2026-11-06: 1レース1回だけ。掲載前でも取り直さない)。
 * 窓は締切の start〜end 分前(既定15〜10)。窓の中で1回の起動の3ページ上限により取り損ねたレースだけ、last 分前(既定5)まで取る。
 * Cron が5分ごとなので、窓の幅(start − end)は5分以上にすること。
 * record: 状態に残っているそのレースの記録(あれば取らない)
 */
export function shouldFetch(minutes, record, window = { start: FIRST_WINDOW[1], end: FIRST_WINDOW[0], last: LAST_CHANCE_MINUTES }) {
  if (record) return false;
  return minutes >= window.last && minutes <= window.start;
}

/** 状態の既定値。 */
export function emptyState() {
  return { count: 0, consecutive_failures: 0, last_dispatch_ms: null, pending_dispatch: [], races: {} };
}

export function waitBeforeNext(lastEndMs, nowMs) {
  return lastEndMs === null ? 0 : Math.max(0, PAGE_INTERVAL_MS - (nowMs - lastEndMs));
}

/** dispatch を今送るか。 */
export function shouldDispatch(pending, lastDispatchMs, nowMs) {
  return pending.length > 0 && (lastDispatchMs === null || nowMs - lastDispatchMs >= DISPATCH_MIN_INTERVAL_MS);
}

/** 締切までの分。 */
export function minutesBefore(closedAtMs, nowMs) {
  return (closedAtMs - nowMs) / 60000;
}
