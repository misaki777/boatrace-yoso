// 公開JSON(docs/data/)の読み込み。仕様は spec/04_export_json.md。
// - ?data=sample で docs/data/sample/ を読む(開発用)。
// - 読み込んだ結果はメモリに持ち、画面の切り替えでは読み直さない(再読み込みボタンで読み直す)。
// - Service Worker がキャッシュから返したときは応答ヘッダ X-SW-Cache: 1 が付く(sw.js)。

export const SCHEMA_VERSION = 1;

// データの置き場所(?data=…)。ローカルJSON(docs/data-local/)はオッズ・期待値の数値を含む(spec/07 5章)
//   sample        → data/sample/(開発用・公開版の形)
//   sample-local  → data/sample-local/(開発用・ローカル版の形)
//   local         → data-local/
//   public        → data/(ローカルを使わない)
//   指定なし       → GitHub Pages では data/。それ以外(PC・同じWi-Fi)では data-local/index.json が読めれば data-local/、読めなければ data/
const params = new URLSearchParams(location.search);
const MODE = params.get('data') || '';
const FIXED_BASES = { sample: 'data/sample/', 'sample-local': 'data/sample-local/', local: 'data-local/', public: 'data/' };
export const isSample = MODE === 'sample' || MODE === 'sample-local';
const autoLocal = !FIXED_BASES[MODE] && !/\.github\.io$/.test(location.hostname);

let DATA_BASE = FIXED_BASES[MODE] || 'data/';
let baseReady = null;

/** 自動の判定(必要なら data-local/index.json を1回だけ確かめる) */
function resolveBase() {
  if (baseReady) return baseReady;
  if (!autoLocal) {
    baseReady = Promise.resolve(DATA_BASE);
    return baseReady;
  }
  baseReady = fetch('data-local/index.json', { cache: 'no-cache' })
    .then((res) => {
      if (res.ok) DATA_BASE = 'data-local/';
      return DATA_BASE;
    })
    .catch(() => DATA_BASE);
  return baseReady;
}

/** ローカル表示か(紫の「⌂ ローカル」を全画面に出す) */
export function isLocalData() {
  return DATA_BASE === 'data-local/' || DATA_BASE === 'data/sample-local/';
}

/** ローカル表示を明示的に選んでいるか(つながらないときに「公開版で見る」を出す) */
export const isLocalMode = MODE === 'local' || MODE === 'sample-local';

/** 今のデータの置き場所(表示用) */
export function dataBase() {
  return DATA_BASE;
}

/** 読み込みの失敗。kind: network / notfound / http / parse / schema / invalid */
export class DataError extends Error {
  constructor(kind, path, detail) {
    super(`${path}: ${kind}${detail !== undefined ? ` (${detail})` : ''}`);
    this.kind = kind;
    this.path = path;
    this.detail = detail;
  }
}

// Service Worker と共通のキャッシュ名(sw.js の DATA_CACHE と合わせる)
export const DATA_CACHE = 'br-data-v1';

const memo = new Map(); // path -> { json, fromCache, revalidating, fetchedAt }
const inflight = new Map(); // path -> Promise
let provisionalListener = null;

/** キャッシュの値を先に表示できるようになったときに呼ばれる関数を登録する */
export function onProvisional(fn) {
  provisionalListener = fn;
}

/** 読み込み済みならその値(無ければ null) */
export function peek(path) {
  return memo.get(path) || null;
}

/**
 * JSON を読む。force=true でメモリの値を使わずに読み直す。
 * 読み直しに失敗したときは、前の値があればそれを stale=true で返す(オフライン表示に使う)。
 */
export function load(path, { force = false, validate } = {}) {
  if (!force && memo.has(path)) return Promise.resolve(memo.get(path));
  if (inflight.has(path)) return inflight.get(path);
  // 初回は端末のキャッシュを先に読み、ネットワークの応答より先に着けば仮に表示する(design/screens README 7.1)
  if (!memo.has(path)) {
    readFromCache(path, validate).then((cached) => {
      if (cached && !memo.has(path) && inflight.has(path)) {
        memo.set(path, cached);
        if (provisionalListener) provisionalListener(path);
      }
    });
  }
  const promise = fetchJson(path, validate)
    .then((entry) => {
      memo.set(path, entry);
      return entry;
    })
    .catch((err) => {
      // 取り直しに失敗しても、前の値があれば表示を保つ(PM決定: 失敗時は表示を保持し「更新できませんでした」)
      const old = memo.get(path);
      if (old) {
        const stale = { ...old, fromCache: old.fromCache || err.kind === 'network', revalidating: false, refreshError: err };
        memo.set(path, stale);
        return stale;
      }
      throw err;
    })
    .finally(() => inflight.delete(path));
  inflight.set(path, promise);
  return promise;
}

async function readFromCache(path, validate) {
  try {
    await resolveBase();
    if (!('caches' in window)) return null;
    const url = new URL(DATA_BASE + path, location.href).href;
    const res = await caches.match(url, { cacheName: DATA_CACHE });
    if (!res) return null;
    const json = await res.json();
    if (!json || json.schema_version !== SCHEMA_VERSION) return null;
    if (validate && validate(json)) return null;
    return { json, fromCache: true, revalidating: true, fetchedAt: 0 };
  } catch (e) {
    return null;
  }
}

/** 最後にネットワークから取れた時刻(ミリ秒。無ければ 0) */
export function lastFetchedAt() {
  let latest = 0;
  memo.forEach((entry) => {
    if (!entry.fromCache && entry.fetchedAt > latest) latest = entry.fetchedAt;
  });
  return latest;
}

async function fetchJson(path, validate) {
  await resolveBase();
  let res;
  try {
    // ブラウザの HTTP キャッシュは再検証させる(GitHub Pages は max-age=600 を返すため)
    res = await fetch(DATA_BASE + path, { cache: 'no-cache' });
  } catch (e) {
    throw new DataError('network', path, e && e.message);
  }
  if (res.status === 404) throw new DataError('notfound', path, 404);
  if (!res.ok) throw new DataError('http', path, res.status);
  // 初回の表示ではまだ Service Worker が通信を仲介していないので、ページ側でもキャッシュに入れておく
  // (次にオフラインで開いたときに表示できるように)
  if ('caches' in window && !(navigator.serviceWorker && navigator.serviceWorker.controller)) {
    const copy = res.clone();
    caches.open(DATA_CACHE).then((c) => c.put(new URL(DATA_BASE + path, location.href).href, copy)).catch(() => {});
  }
  let json;
  try {
    json = await res.json();
  } catch (e) {
    throw new DataError('parse', path);
  }
  if (!json || json.schema_version !== SCHEMA_VERSION) {
    throw new DataError('schema', path, json ? json.schema_version : undefined);
  }
  if (validate) {
    const problem = validate(json);
    if (problem) throw new DataError('invalid', path, problem);
  }
  const fromCache = res.headers.get('X-SW-Cache') === '1';
  return { json, fromCache, revalidating: false, fetchedAt: fromCache ? 0 : Date.now(), refreshError: null };
}

// ---- 最小限の構造チェック(画面が壊れないことだけを見る。厳密な検証は src/export/schema.py) ----

export function validateIndex(j) {
  if (!Array.isArray(j.dates)) return 'dates がありません';
  if (j.dates.some((d) => !d || typeof d.date !== 'string' || typeof d.file !== 'string')) return 'dates の形式が違います';
  return null;
}

export function validateDay(j) {
  if (typeof j.date !== 'string') return 'date がありません';
  if (!Array.isArray(j.stadiums)) return 'stadiums がありません';
  if (j.stadiums.some((s) => !Array.isArray(s.races))) return 'races がありません';
  return null;
}

export function validatePerformance(j) {
  if (typeof j !== 'object') return '形式が違います';
  return null;
}

export const loadIndex = (opts = {}) => load('index.json', { ...opts, validate: validateIndex });
export const loadDay = (file, opts = {}) => load(file, { ...opts, validate: validateDay });
export const loadPerformance = (opts = {}) => load('performance.json', { ...opts, validate: validatePerformance });

/** 失敗の内容を、利用者にわかる言葉にする */
export function describeError(err) {
  if (!(err instanceof DataError)) {
    return { title: 'データを読み込めませんでした', text: '画面の表示中に問題が起きました。もう一度読み込んでください。', detail: String(err && err.message) };
  }
  const file = err.path;
  switch (err.kind) {
    case 'network':
      return { title: 'データを読み込めませんでした', text: '通信状態を確認して、もう一度読み込んでください。前回のデータも端末に残っていません。', detail: `${file} の取得に失敗(ネットワークエラー)` };
    case 'notfound':
      return { title: 'データが見つかりません', text: 'まだ作成されていないか、削除された可能性があります。', detail: `${file} が見つかりません(404)` };
    case 'http':
      return { title: 'データを読み込めませんでした', text: 'サーバーが応答しませんでした。時間をおいて、もう一度読み込んでください。', detail: `${file} の取得に失敗(HTTP ${err.detail})` };
    case 'parse':
    case 'invalid':
      return { title: 'データの形式が正しくありません', text: 'データの作成中だった可能性があります。少し待ってから、もう一度読み込んでください。', detail: `${file}: ${err.detail || '読み取れません'}` };
    case 'schema':
      return { title: 'アプリの更新が必要です', text: `データの形式が新しくなりました(schema_version ${err.detail ?? '不明'})。アプリを再読み込みすると最新版に切り替わります。`, detail: `このアプリが読めるのは schema_version ${SCHEMA_VERSION}`, needsAppUpdate: true };
    default:
      return { title: 'データを読み込めませんでした', text: 'もう一度読み込んでください。', detail: file };
  }
}
