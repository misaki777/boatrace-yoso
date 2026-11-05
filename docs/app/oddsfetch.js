// 中継 Worker からの締切前オッズの取得(spec/07 9章、W-10)。応答の形は workers/odds-proxy/README.md。
// - 取得したオッズは端末のメモリと sessionStorage にだけ置く(localStorage・外部には保存・送信しない)。日付が変わったら捨てる。
// - 同じレースの取り直しは60秒たつまで押せない。502・通信失敗・タイムアウトの後は10秒後から再試行できる。
// - 429(1日の上限)・503(停止中)を受けたら、その日はすべてのレースで取得しない。
// - 45秒で打ち切る(AbortController)。
import { todayJst, nowMs, parseIso } from './time.js';
import { getWorkerConfig, getWorkerSecret } from './settings.js';

const STORE_KEY = 'br.odds.v1';
export const COOLDOWN_MS = 60 * 1000;
export const RETRY_AFTER_ERROR_MS = 10 * 1000;
export const TIMEOUT_MS = 45 * 1000;
/** 自動取得の条件: 締切まで15分以内 */
export const AUTO_WITHIN_MS = 15 * 60 * 1000;
/** 1ページあたりの目安の秒数(表示用) */
export const SECONDS_PER_PAGE = 10;

/** 券種 → Worker のページ種別 */
export const TYPE_OF_BET = { trifecta: '3t', exacta: '2tf', win: 'tf' };
export const PAGE_LABELS = { '3t': '3連単', '2tf': '2連単・2連複', tf: '単勝・複勝' };
const PAGE_ORDER = ['3t', '2tf', 'tf'];

const inflight = new Map(); // race_id -> { controller, startedAt, types, timedOut, canceled }
let listener = null;

export function onOddsChange(fn) {
  listener = fn;
}
function notify() {
  if (listener) listener();
}

// ---------------------------------------------------------------- sessionStorage

function emptyStore() {
  return { date: todayJst(), races: {}, block: null };
}

function readStore() {
  try {
    const v = JSON.parse(sessionStorage.getItem(STORE_KEY) || 'null');
    if (!v || typeof v !== 'object' || v.date !== todayJst() || typeof v.races !== 'object') return emptyStore();
    return v;
  } catch (e) {
    return emptyStore();
  }
}

function writeStore(store) {
  try {
    sessionStorage.setItem(STORE_KEY, JSON.stringify(store));
  } catch (e) {
    // 保存できなくても、メモリの上では続ける
  }
}

let memoryStore = null;
function store() {
  if (!memoryStore || memoryStore.date !== todayJst()) memoryStore = readStore();
  return memoryStore;
}
function save() {
  writeStore(memoryStore);
}

// ---------------------------------------------------------------- 状態の取り出し

/**
 * レースの取得の状態。
 * { loading: {startedAt, types} | null, result: {response, receivedAt} | null, error: {...} | null,
 *   block: {kind} | null, waitMs: 押せるようになるまでのミリ秒 }
 */
export function oddsState(raceId) {
  const s = store();
  const r = s.races[raceId] || {};
  const run = inflight.get(raceId);
  const now = Date.now();
  let waitMs = 0;
  if (r.requestedAt) {
    const gap = r.error && ['upstream', 'network', 'timeout', 'offline', 'canceled'].includes(r.error.kind) ? RETRY_AFTER_ERROR_MS : COOLDOWN_MS;
    // キャッシュから返ったときは、キャッシュの元の取得時刻から60秒
    let base = r.requestedAt;
    if (!r.error && r.result && r.result.response && r.result.response.cached) {
      base = Math.min(base, r.result.receivedAt - (r.result.response.cache_age_sec || 0) * 1000);
    }
    waitMs = Math.max(0, base + gap - now);
  }
  return {
    loading: run ? { startedAt: run.startedAt, types: run.types } : null,
    result: r.result || null,
    error: r.error || null,
    block: s.block,
    waitMs,
  };
}

/** Worker の応答の odds(手入力より下・公開ランクより上の出どころ)。無ければ null */
export function workerOddsInput(raceId) {
  const r = store().races[raceId];
  return r && r.result && r.result.response && r.result.response.odds ? { odds: r.result.response.odds } : null;
}

/** その日のどこかのレースで取得したことがあるか(ローカルに数値がある) */
export function hasAnyWorkerOdds() {
  return Object.values(store().races).some((r) => r.result);
}

// ---------------------------------------------------------------- 取得

/** 要求するページ: 設定の券種に対応するものだけ(公式サイトへの要求を最小にする) */
export function pagesFor(betTypes) {
  const set = new Set(Object.keys(betTypes || {}).map((b) => TYPE_OF_BET[b]).filter(Boolean));
  if (!set.size) set.add('3t');
  return PAGE_ORDER.filter((p) => set.has(p));
}

/** 取得できるか。できないときは理由 */
export function canFetch(race, raceId) {
  const cfg = getWorkerConfig();
  if (!cfg.ready) return { ok: false, reason: 'nokey' };
  const close = parseIso(race.closed_at);
  if (Number.isNaN(close) || close <= nowMs()) return { ok: false, reason: 'closed' };
  if (raceId.slice(0, 8) !== todayJst().replace(/-/g, '')) return { ok: false, reason: 'not_today' };
  const st = oddsState(raceId);
  if (st.block) return { ok: false, reason: st.block.kind };
  if (st.loading) return { ok: false, reason: 'loading' };
  if (typeof navigator !== 'undefined' && navigator.onLine === false) return { ok: false, reason: 'offline' };
  if (st.waitMs > 0) return { ok: false, reason: 'cooldown', waitMs: st.waitMs };
  return { ok: true };
}

/** 自動取得の条件(設定がオン・締切まで15分以内・60秒以内に取っていない) */
export function shouldAutoFetch(race, raceId) {
  const cfg = getWorkerConfig();
  if (!cfg.auto) return false;
  const st = oddsState(raceId);
  if (st.result || st.error) return false; // 一度取ったら(失敗も含めて)自動では繰り返さない
  const close = parseIso(race.closed_at);
  if (Number.isNaN(close) || close - nowMs() > AUTO_WITHIN_MS) return false;
  return canFetch(race, raceId).ok;
}

const ERROR_KINDS = {
  400: 'bad_request', 401: 'unauthorized', 403: 'forbidden_origin', 404: 'not_found', 405: 'bad_request',
  409: 'closed', 429: 'daily_limit', 500: 'server_misconfigured', 502: 'upstream', 503: 'stopped',
};

/** 取得を始める。race は days の race、betTypes は設定の券種 */
export function startFetch(race, betTypes) {
  const raceId = race.race_id;
  const check = canFetch(race, raceId);
  if (!check.ok) return check;
  const cfg = getWorkerConfig();
  const key = getWorkerSecret();
  const types = pagesFor(betTypes);
  const m = /^(\d{8})-(\d{2})-(\d{2})$/.exec(raceId);
  const params = `date=${m[1]}&jcd=${m[2]}&rno=${Number(m[3])}&types=${types.join(',')}&closed_at=${encodeURIComponent(race.closed_at)}`;
  const url = `${cfg.url.replace(/\/+$/, '')}/odds?${params}`;
  const controller = new AbortController();
  const run = { controller, startedAt: Date.now(), types, timedOut: false, canceled: false };
  inflight.set(raceId, run);
  const s = store();
  s.races[raceId] = { ...(s.races[raceId] || {}), requestedAt: Date.now() };
  save();
  const timer = setTimeout(() => {
    run.timedOut = true;
    controller.abort();
  }, TIMEOUT_MS);
  notify();

  // 合言葉はヘッダでだけ送る(URL に入れない)
  fetch(url, { headers: { 'X-Odds-Key': key }, signal: controller.signal, cache: 'no-store', credentials: 'omit' })
    .then(async (res) => {
      let body = null;
      try {
        body = await res.json();
      } catch (e) {
        body = null;
      }
      if (res.ok && body && body.ok === true && body.odds && typeof body.odds === 'object') {
        return { result: { response: sanitizeResponse(body), receivedAt: Date.now(), types } };
      }
      const kind = res.ok ? 'invalid' : (ERROR_KINDS[res.status] || 'upstream');
      return { error: { kind, status: res.status, quota: body && body.quota ? body.quota : null } };
    })
    .catch((e) => {
      if (run.canceled) return { error: { kind: 'canceled' } };
      if (run.timedOut) return { error: { kind: 'timeout' } };
      return { error: { kind: navigator.onLine === false ? 'offline' : 'network' } };
    })
    .then((outcome) => {
      clearTimeout(timer);
      inflight.delete(raceId);
      const st = store();
      const prev = st.races[raceId] || {};
      if (outcome.result) {
        st.races[raceId] = { requestedAt: prev.requestedAt, result: outcome.result, error: null };
      } else {
        // 失敗しても、前に取れたオッズがあれば残す(表示は前の出どころのまま)
        st.races[raceId] = { requestedAt: outcome.error.kind === 'canceled' ? null : prev.requestedAt, result: prev.result || null, error: { ...outcome.error, at: Date.now() } };
        if (outcome.error.kind === 'daily_limit' || outcome.error.kind === 'stopped') st.block = { kind: outcome.error.kind, quota: outcome.error.quota || null };
      }
      save();
      notify();
    });
  return { ok: true };
}

/** 取得を取り消す */
export function cancelFetch(raceId) {
  const run = inflight.get(raceId);
  if (!run) return;
  run.canceled = true;
  run.controller.abort();
}

/** 応答から必要な項目だけを残す(メッセージなどは保存しない) */
function sanitizeResponse(b) {
  return {
    race_id: b.race_id,
    fetched_at: b.fetched_at,
    cached: b.cached === true,
    cache_age_sec: typeof b.cache_age_sec === 'number' ? b.cache_age_sec : 0,
    pages: b.pages && typeof b.pages === 'object' ? b.pages : {},
    odds_time_label: typeof b.odds_time_label === 'string' ? b.odds_time_label : '',
    is_final_odds: b.is_final_odds === true,
    odds: b.odds,
    quota: b.quota || null,
  };
}

/** 一部の券種が取れなかったか(要求したページのうち ok / cached / skipped でないものがある) */
export function failedPages(result) {
  if (!result) return [];
  const pages = result.response.pages || {};
  return (result.types || Object.keys(pages)).filter((p) => !['ok', 'cached', 'skipped'].includes(pages[p]));
}
