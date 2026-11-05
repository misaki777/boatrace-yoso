// 端末内のデータ(IndexedDB `br-yoso`。spec/08 5.4)。購入記録・印・メモ・予算。
// これらは公開JSON・サーバー・Worker に送らない。バックアップは JSON ファイルの書き出し・読み込みだけ。
const DB_NAME = 'br-yoso';
const DB_VERSION = 1;
export const STORES = ['bets', 'marks', 'notes', 'budget'];
const KEYS = { bets: 'id', marks: 'race_id', notes: 'race_id', budget: 'key' };
export const BACKUP_FORMAT = 'br-yoso-backup';

let dbPromise = null;
function openDb() {
  if (dbPromise) return dbPromise;
  dbPromise = new Promise((resolve, reject) => {
    if (!('indexedDB' in window)) { reject(new Error('この端末では IndexedDB が使えません')); return; }
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = () => {
      const db = req.result;
      STORES.forEach((s) => { if (!db.objectStoreNames.contains(s)) db.createObjectStore(s, { keyPath: KEYS[s] }); });
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
  return dbPromise;
}

function run(store, mode, fn) {
  return openDb().then((db) => new Promise((resolve, reject) => {
    const tx = db.transaction(store, mode);
    const os = tx.objectStore(store);
    const req = fn(os);
    tx.oncomplete = () => resolve(req ? req.result : undefined);
    tx.onerror = () => reject(tx.error);
    tx.onabort = () => reject(tx.error);
  }));
}

export const getAll = (store) => run(store, 'readonly', (os) => os.getAll());
export const get = (store, key) => run(store, 'readonly', (os) => os.get(key));
export const put = (store, value) => run(store, 'readwrite', (os) => os.put(value));
export const remove = (store, key) => run(store, 'readwrite', (os) => os.delete(key));

/** 購入記録の id を作る(端末内で一意なら良い) */
export function newId() {
  return `b${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`;
}

/** 全部を JSON にする(バックアップ) */
export async function exportAll() {
  const out = { format: BACKUP_FORMAT, version: 1, exported_at: new Date().toISOString() };
  for (const s of STORES) out[s] = await getAll(s);
  return out;
}

/** バックアップの形を確かめる。問題があれば理由の文字列 */
export function validateBackup(data) {
  if (!data || typeof data !== 'object' || data.format !== BACKUP_FORMAT) return 'このアプリのバックアップファイルではありません';
  for (const s of STORES) {
    if (data[s] !== undefined && !Array.isArray(data[s])) return `${s} の形が違います`;
    if ((data[s] || []).some((x) => !x || typeof x !== 'object' || x[KEYS[s]] === undefined)) return `${s} に読めない項目があります`;
  }
  return null;
}

/** 読み込む(同じキーは上書き、それ以外は残す=追加して合わせる)。読み込んだ件数を返す */
export async function importAll(data) {
  const problem = validateBackup(data);
  if (problem) throw new Error(problem);
  const counts = {};
  for (const s of STORES) {
    counts[s] = 0;
    for (const item of data[s] || []) { await put(s, item); counts[s] += 1; }
  }
  return counts;
}
