// 端末に保存する設定(S6)。
// - 推奨買い目の決め方: localStorage のキー `settings.v1`(spec/07 4.2)。壊れていれば既定に戻す。
// - 表示の設定(表示モード・お気に入りの場・確率上位に出す券種): `br.display.v1`
// - Worker(URL・合言葉・自動取得): `br.worker.v1`。合言葉は画面に戻さない(保存済みかどうかだけ返す)
// - 手入力のオッズ(S7): `br.manual.v1`(race_id ごと。spec/05 S7)
import { normalizeSettingsDetail, defaultSettings, sameSettings } from './strategy.js';

const SETTINGS_KEY = 'settings.v1';
const DISPLAY_KEY = 'br.display.v1';
const WORKER_KEY = 'br.worker.v1';
const MANUAL_KEY = 'br.manual.v1';

function readJson(key) {
  try {
    const text = localStorage.getItem(key);
    if (text === null) return { exists: false, value: undefined };
    try {
      return { exists: true, value: JSON.parse(text) };
    } catch (e) {
      return { exists: true, value: undefined, broken: true };
    }
  } catch (e) {
    return { exists: false, value: undefined };
  }
}

function writeJson(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
    return true;
  } catch (e) {
    return false;
  }
}

// ---------------------------------------------------------------- 推奨買い目の設定

let cached = null;
/** 読み込んだときに既定へ戻した(保存されていた設定を読めなかった)か。S6 で1回だけ知らせる */
let resetNotice = false;

export function getSettings() {
  if (cached) return cached;
  const stored = readJson(SETTINGS_KEY);
  if (!stored.exists) {
    cached = defaultSettings();
    return cached;
  }
  const { settings, replaced } = normalizeSettingsDetail(stored.broken ? '(壊れた値)' : stored.value);
  cached = settings;
  if (replaced || stored.broken) {
    resetNotice = true;
    writeJson(SETTINGS_KEY, settings);
  }
  return cached;
}

export function saveSettings(next) {
  const { settings } = normalizeSettingsDetail(next);
  cached = settings;
  writeJson(SETTINGS_KEY, settings);
  return settings;
}

export function resetSettings() {
  return saveSettings(defaultSettings());
}

/** 既定から変えているか(stake も含む) */
export function isCustomSettings() {
  return !sameSettings(getSettings(), defaultSettings());
}

/** 既定に戻した知らせを1回だけ取り出す */
export function takeResetNotice() {
  const v = resetNotice;
  resetNotice = false;
  return v;
}

// ---------------------------------------------------------------- 表示の設定

const DEFAULT_DISPLAY = { theme: 'auto', favorites: [], rank_types: ['win', 'exacta', 'trifecta'], filters: { kata: false, recommended: false, favorites: false } };

export function getDisplay() {
  const v = readJson(DISPLAY_KEY).value;
  const out = JSON.parse(JSON.stringify(DEFAULT_DISPLAY));
  if (v && typeof v === 'object') {
    if (['auto', 'light', 'dark'].includes(v.theme)) out.theme = v.theme;
    if (Array.isArray(v.favorites)) out.favorites = v.favorites.filter((n) => Number.isInteger(n) && n >= 1 && n <= 24);
    if (Array.isArray(v.rank_types)) {
      const types = v.rank_types.filter((t) => DEFAULT_DISPLAY.rank_types.includes(t));
      if (types.length) out.rank_types = types;
    }
    if (v.filters && typeof v.filters === 'object') {
      Object.keys(out.filters).forEach((k) => { out.filters[k] = v.filters[k] === true; });
    }
  }
  return out;
}

export function saveDisplay(next) {
  writeJson(DISPLAY_KEY, next);
  applyTheme(next.theme);
}

/** 表示モード: <html data-theme> を付け外しする(tokens.css が対応) */
export function applyTheme(theme) {
  const root = document.documentElement;
  if (theme === 'light' || theme === 'dark') root.setAttribute('data-theme', theme);
  else root.removeAttribute('data-theme');
}

// ---------------------------------------------------------------- Worker の設定

/** Worker の設定。合言葉は値を返さず hasKey だけ返す */
export function getWorkerConfig() {
  const v = readJson(WORKER_KEY).value || {};
  const url = typeof v.url === 'string' ? v.url : '';
  return { url, hasKey: typeof v.key === 'string' && v.key.length > 0, auto: v.auto === true, ready: !!url && typeof v.key === 'string' && v.key.length > 0 };
}

/** 合言葉を含む設定(Worker への要求にだけ使う。画面には出さない) */
export function getWorkerSecret() {
  const v = readJson(WORKER_KEY).value || {};
  return typeof v.key === 'string' ? v.key : '';
}

export function saveWorkerConfig(patch) {
  const v = readJson(WORKER_KEY).value || {};
  const next = { url: typeof v.url === 'string' ? v.url : '', key: typeof v.key === 'string' ? v.key : '', auto: v.auto === true };
  if ('url' in patch) next.url = String(patch.url || '').trim();
  if ('key' in patch) next.key = String(patch.key || '');
  if ('auto' in patch) next.auto = patch.auto === true;
  writeJson(WORKER_KEY, next);
}

/** Worker の URL の形を確かめる(http(s) の絶対 URL)。空は可 */
export function validWorkerUrl(text) {
  if (!text) return true;
  try {
    const u = new URL(text);
    return (u.protocol === 'https:' || u.protocol === 'http:') && !u.search && !u.hash;
  } catch (e) {
    return false;
  }
}

// ---------------------------------------------------------------- 手入力のオッズ(S7)

const MANUAL_KEEP_DAYS = 7;

function readManualAll() {
  const v = readJson(MANUAL_KEY).value;
  return v && typeof v === 'object' && !Array.isArray(v) ? v : {};
}

/** レースの手入力のオッズ: { 券種: { 組合せ: オッズ } } */
export function getManualOdds(raceId) {
  const all = readManualAll();
  const v = all[raceId];
  return v && typeof v === 'object' ? v : {};
}

/** レースの手入力をすべて消す */
export function clearManualOdds(raceId) {
  const all = readManualAll();
  delete all[raceId];
  writeJson(MANUAL_KEY, all);
}

/** 手入力を保存する。odds が null なら消す */
export function setManualOdds(raceId, betType, combination, odds) {
  const all = readManualAll();
  const race = all[raceId] && typeof all[raceId] === 'object' ? all[raceId] : {};
  const table = race[betType] && typeof race[betType] === 'object' ? race[betType] : {};
  if (odds === null) delete table[combination];
  else table[combination] = odds;
  if (Object.keys(table).length) race[betType] = table;
  else delete race[betType];
  if (Object.keys(race).length) all[raceId] = race;
  else delete all[raceId];
  // 古いレースの手入力は消す(race_id の先頭8桁の日付で判断)
  const limit = Date.now() - MANUAL_KEEP_DAYS * 86400000;
  Object.keys(all).forEach((id) => {
    const m = /^(\d{4})(\d{2})(\d{2})-/.exec(id);
    if (m && Date.UTC(+m[1], +m[2] - 1, +m[3]) < limit) delete all[id];
  });
  writeJson(MANUAL_KEY, all);
}
