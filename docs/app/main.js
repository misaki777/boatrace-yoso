// アプリの入口: ハッシュのルーティング、データの読み込みの段取り、画面の描画、操作の受け付け。
// ルート: #/ (今日)、#/date/YYYY-MM-DD、#/race/<race_id>、#/performance、#/about、#/settings(#/settings/worker)
import { html, toHtmlString } from './html.js';
import { initClock, todayJst, fmtUpdated, fmtMonthDay, isClockFixed } from './time.js';
import { fmtRoi } from './format.js';
import {
  peek, load, loadIndex, loadDay, loadPerformance, describeError, onProvisional, lastFetchedAt, isLocalData, isLocalMode, dataBase,
} from './data.js';
import {
  getSettings, saveSettings, resetSettings, isCustomSettings, takeResetNotice, getDisplay, saveDisplay, applyTheme,
  getWorkerConfig, saveWorkerConfig, validWorkerUrl, setManualOdds, clearManualOdds,
} from './settings.js';
import { CONSTANTS, computeRecommendation } from './strategy.js';
import { startFetch, cancelFetch, onOddsChange, shouldAutoFetch, oddsState } from './oddsfetch.js';
import { fetchPanel, raceRecommendation, describeSettings, localMark } from './odds-ui.js';
import { settingsView } from './views/settings.js';
import * as localdb from './localdb.js';
import { summarize, byMonth, budgetWarnings, normalizeBudget, normalizeMarks, markStats } from './ledger.js';
import { ledgerView, marksView, analysisView } from './views/ledger.js';
import { groupStats, timeBand, evRankKey, allocationKey } from './analytics.js';
import { racerSheet, modelsView } from './views/phase3b.js';
import { stadiumTab, playersTab, modelTab, simTab } from './views/analytics.js';
import { ICONS, tabbar } from './parts.js';
import { copyText, ALLOCATION_METHODS } from './betsum.js';
import { currentAllocation } from './views/race.js';
import { t, WORDING } from './wording.js';
import { betTypeLabel } from './format.js';
import { jstDateString, parseIso, nowMs } from './time.js';
import { getPref, setPref, session } from './store.js';
import { emptyBox } from './parts.js';
import { parseRaceId, findRace } from './model.js';
import { homeView, homeSkeleton, noTodayView, homeErrorView } from './views/home.js';
import { raceView, raceSkeleton, raceErrorView, homeHref } from './views/race.js';
import { performanceView, performanceSkeleton, performanceErrorView, chartSvg } from './views/performance.js';
import { aboutView, datesSheet } from './views/about.js';

initClock(location.search);
applyTheme(getDisplay().theme);

const appEl = document.getElementById('app');
const overlayEl = document.getElementById('overlay');

const state = {
  busy: false, // 再読み込み中
  errors: new Map(), // path -> DataError
  pending: new Set(), // 読み込み中の path
  sheetOpen: false,
  lastHash: null,
  restoreScroll: null, // 描画後に戻すスクロール位置
};

// ---------------------------------------------------------------- ルーティング

function parseRoute() {
  const hash = location.hash.replace(/^#/, '') || '/';
  let m;
  if (hash === '/' || hash === '') return { name: 'home', date: null };
  if ((m = /^\/date\/(\d{4}-\d{2}-\d{2})$/.exec(hash))) return { name: 'home', date: m[1] };
  if ((m = /^\/race\/([0-9-]+)$/.exec(hash))) return { name: 'race', raceId: m[1] };
  if (hash === '/performance') return { name: 'performance' };
  if (hash === '/about') return { name: 'about' };
  if (hash === '/ledger') return { name: 'ledger' };
  if (hash === '/models') return { name: 'models' };
  if (hash === '/analysis') return { name: 'analytics', sub: 'you' };
  if ((m = /^\/analytics\/(stadium|players|model|sim|you)$/.exec(hash))) return { name: 'analytics', sub: m[1] };
  if (hash === '/analytics') return { name: 'analytics', sub: 'stadium' };
  if (hash === '/marks') return { name: 'marks' };
  if (hash === '/settings' || hash === '/settings/worker') return { name: 'settings', focus: hash.endsWith('/worker') ? 'worker' : null };
  return { name: 'unknown' };
}

// ---------------------------------------------------------------- 読み込みの段取り

/** まだ読んでいなければ読み込みを始める(描画は読み込み後にもう一度行う) */
function request(path, loader) {
  if (peek(path) && !peek(path).revalidating) return;
  if (state.errors.has(path) || state.pending.has(path)) return;
  state.pending.add(path);
  loader()
    .catch((err) => {
      console.warn('読み込み失敗', err);
      state.errors.set(path, err);
    })
    .finally(() => {
      state.pending.delete(path);
      render();
    });
}

const requestIndex = () => request('index.json', () => loadIndex());
const requestDay = (file) => request(file, () => loadDay(file));
const requestPerformance = () => request('performance.json', () => loadPerformance());

// キャッシュの値がネットワークより先に読めたら、それで描いておく
onProvisional(() => render());

/** 日付 → days ファイル名(index にあればそれを使う) */
function dayFileFor(date) {
  const idx = peek('index.json');
  const info = idx && idx.json.dates.find((d) => d.date === date);
  return info ? info.file : `days/${date.replace(/-/g, '')}.json`;
}

/** 最新の日のデータ(読み込み済みなら) */
function latestDayJson() {
  const idx = peek('index.json');
  if (!idx || !idx.json.dates.length) return null;
  const entry = peek(idx.json.dates[0].file);
  return entry ? entry.json : null;
}

/** 表示中の画面が使うファイル */
function pathsForRoute(route) {
  const paths = ['index.json'];
  if (route.name === 'home') {
    const idx = peek('index.json');
    const date = route.date || (idx ? homeDate(idx.json).date : null);
    if (date) paths.push(dayFileFor(date));
  } else if (route.name === 'race') {
    const parsed = parseRaceId(route.raceId);
    if (parsed) paths.push(dayFileFor(parsed.date));
  } else if (route.name === 'performance') {
    paths.push('performance.json');
  }
  return paths;
}

/** 再読み込み(ボタン・前面復帰・通信の回復) */
async function refresh() {
  if (state.busy) return;
  state.busy = true;
  render();
  const route = parseRoute();
  const paths = pathsForRoute(route);
  paths.forEach((p) => state.errors.delete(p));
  const loaders = paths.map((p) => {
    if (p === 'index.json') return loadIndex({ force: true });
    if (p === 'performance.json') return loadPerformance({ force: true });
    return loadDay(p, { force: true });
  });
  const results = await Promise.allSettled(loaders);
  results.forEach((r, i) => {
    if (r.status === 'rejected') state.errors.set(paths[i], r.reason);
  });
  // index を読み直したら、表示中の日のファイル名が変わっていないかも確かめる
  const again = pathsForRoute(route).filter((p) => !paths.includes(p));
  await Promise.allSettled(again.map((p) => loadDay(p, { force: true }).catch((e) => state.errors.set(p, e))));
  state.busy = false;
  loadStatus();
  render();
}

// ---------------------------------------------------------------- 帯(オフライン・更新失敗)

function statusBanners(entries) {
  const shown = entries.filter(Boolean);
  const stale = shown.find((e) => e.fromCache && !e.revalidating);
  const failed = shown.find((e) => e.refreshError);
  if (!stale && !failed) return '';
  const base = stale || failed;
  const at = fmtUpdated(base.json.generated_at, todayJst());
  const offline = stale && (navigator.onLine === false || (base.refreshError ? base.refreshError.kind === 'network' : true));
  if (offline) {
    return html`<div class="banner banner--warn" role="status"><span class="banner__icon" aria-hidden="true">⚠</span>
      <div class="banner__body"><b class="banner__title">オフライン表示中(${at}時点)</b><span class="banner__text">通信できないため、前回読み込んだデータを表示しています。締切までの残り時間は端末の時計で更新しています。</span></div>
      <button class="banner__action btn btn--small" data-action="reload">再試行</button></div>`;
  }
  const info = describeError(failed.refreshError);
  return html`<div class="banner banner--warn" role="status"><span class="banner__icon" aria-hidden="true">⚠</span>
    <div class="banner__body"><b class="banner__title">更新できませんでした(${at}時点のデータを表示中)</b><span class="banner__text">${info.needsAppUpdate ? 'データの形式が新しくなりました。アプリを再読み込みしてください。' : info.text}</span></div>
    <button class="banner__action btn btn--small" data-action="${info.needsAppUpdate ? 'reload-app' : 'reload'}">${info.needsAppUpdate ? 'アプリを再読み込み' : '再試行'}</button></div>`;
}

/** 読み込み失敗の箱 */
function errorBox(err, { title } = {}) {
  const info = describeError(err);
  // ローカル表示を選んでいて PC につながらない(design/screens README 12.3 local-error)
  if (isLocalMode && err && (err.kind === 'network' || err.kind === 'notfound' || err.kind === 'http')) {
    const url = new URL(location.href);
    url.searchParams.set('data', 'public');
    return emptyBox({
      icon: '⚠', title: 'PCのデータにつながりません', role: 'alert',
      text: 'PC で http.server が動いているか、同じ Wi-Fi につながっているかを確かめてください。ローカル表示はオフラインでは使えません。',
      detail: info.detail,
      action: html`<button class="btn btn--primary" data-action="reload">再読み込み</button> <a class="btn" href="${url.pathname + url.search + url.hash}">公開版のデータで見る</a>`,
    });
  }
  return emptyBox({
    icon: info.needsAppUpdate ? '⟳' : '⚠',
    title: title || info.title,
    text: info.text,
    detail: info.detail,
    role: 'alert',
    action: info.needsAppUpdate
      ? html`<button class="btn btn--primary" data-action="reload-app">アプリを再読み込み</button>`
      : html`<button class="btn btn--primary" data-action="reload">再読み込み</button>`,
  });
}

// ---------------------------------------------------------------- 各画面

/** S1 で表示する日付を決める。今日のデータが無く、最新が今日より前なら noToday */
function homeDate(index) {
  const today = todayJst();
  if (index.dates.some((d) => d.date === today)) return { date: today, noToday: false };
  const latest = index.latest_date || (index.dates[0] && index.dates[0].date);
  if (!latest || latest < today) return { date: null, noToday: true, latest };
  return { date: latest, noToday: false };
}

function renderHome(route) {
  const idxEntry = peek('index.json');
  const today = todayJst();
  if (!idxEntry) {
    const err = state.errors.get('index.json');
    if (err) return homeErrorView({ date: route.date || today, busy: state.busy, banners: '', box: errorBox(err) });
    requestIndex();
    return homeSkeleton({ date: route.date, busy: true });
  }
  const index = idxEntry.json;
  if (route.date === today) {
    // 今日を選んだら #/ に戻す
    history.replaceState(null, '', '#/');
  }
  let date = route.date;
  if (!date) {
    const d = homeDate(index);
    if (d.noToday) return noTodayView({ today, latestDate: d.latest, index, busy: state.busy, banners: statusBanners([idxEntry]) });
    date = d.date;
  }
  const isToday = date === today;
  const info = index.dates.find((d) => d.date === date);
  if (!info) {
    return homeErrorView({
      date, busy: state.busy, banners: statusBanners([idxEntry]),
      box: emptyBox({ icon: '—', title: 'この日付のデータはありません', text: '日付ボタンから、ほかの日を選んでください。', action: html`<a class="btn" href="#/">今日へ</a>` }),
    });
  }
  const dayEntry = peek(info.file);
  if (!dayEntry) {
    const err = state.errors.get(info.file);
    if (err) return homeErrorView({ date, busy: state.busy, banners: statusBanners([idxEntry]), box: errorBox(err, { title: err.kind === 'schema' ? undefined : 'この日のデータを読み込めませんでした' }) });
    requestDay(info.file);
    return homeSkeleton({ date, busy: true });
  }
  if (dayEntry.revalidating) requestDay(info.file);
  const modeKey = isToday ? 'viewToday' : 'viewPast';
  const mode = getPref(modeKey, isToday ? 'deadline' : 'stadium');
  return homeView({
    date, dayInfo: info, day: dayEntry.json, isToday, mode,
    closedOpen: session.closedOpen[date],
    focusStadium: session.focusStadium,
    marksOf: (id) => (local.loaded ? local.marks[id] : null),
    busy: state.busy,
    banners: statusBanners([idxEntry, dayEntry]),
    ready: true,
  });
}

function renderRace(route) {
  const parsed = parseRaceId(route.raceId);
  const today = todayJst();
  if (!parsed) {
    return raceErrorView({ title: 'レース', backHref: '#/', banners: '', box: emptyBox({ icon: '—', title: 'このレースは見つかりませんでした', text: 'URL を確認してください。', action: html`<a class="btn" href="#/">一覧へ戻る</a>` }) });
  }
  const isToday = parsed.date === today;
  const backHref = homeHref(parsed.date, isToday);
  const title = `${parsed.raceNumber}R`;
  if (!peek('index.json') && !state.errors.has('index.json')) requestIndex();
  const file = dayFileFor(parsed.date);
  const dayEntry = peek(file);
  if (!dayEntry) {
    const err = state.errors.get(file);
    if (err) return raceErrorView({ title, backHref, banners: '', box: errorBox(err, { title: err.kind === 'notfound' ? 'このレースは見つかりませんでした' : undefined }) });
    requestDay(file);
    return raceSkeleton({ title: `読み込み中 ${title}`, backHref });
  }
  if (dayEntry.revalidating) requestDay(file);
  const day = dayEntry.json;
  const found = findRace(day, route.raceId);
  if (!found) {
    return raceErrorView({ title, backHref, banners: statusBanners([dayEntry]), box: emptyBox({ icon: '—', title: 'このレースは見つかりませんでした', text: 'データが更新された可能性があります。', action: html`<a class="btn" href="${backHref}">一覧へ戻る</a>` }) });
  }
  const id = route.raceId;
  const folds = {};
  Object.keys(session.folds).forEach((k) => {
    if (k.startsWith(`${id}:`)) folds[k.slice(id.length + 1)] = session.folds[k];
  });
  return raceView({
    day, found, date: parsed.date, isToday, busy: state.busy,
    banners: statusBanners([dayEntry]),
    prefs: { rankTab: getPref('rankTab', null), playersOpen: getPref('playersOpen', false), rankTypes: getDisplay().rank_types },
    ui: { rankMore: !!session.rankMore[id], folds, manualMode: session.manualMode, local: local.loaded ? local : null, betUnselected: session.betUnselected, alloc: getAlloc(), raceTitle: `${fmtMonthDay(parsed.date)} ${found.stadium.stadium_name} ${found.race.race_number}R` },
  });
}

function renderPerformance() {
  const entry = peek('performance.json');
  if (!peek('index.json') && !state.errors.has('index.json')) requestIndex();
  if (!entry) {
    const err = state.errors.get('performance.json');
    if (err) return performanceErrorView({ busy: state.busy, box: errorBox(err, { title: err.kind === 'schema' ? undefined : '成績を読み込めませんでした' }) });
    requestPerformance();
    return performanceSkeleton();
  }
  if (entry.revalidating) requestPerformance();
  // 採用中の戦略・モデルの情報は、最新の日のファイルから取る(読み込み済みなら)
  const idx = peek('index.json');
  if (idx && idx.json.dates.length && !peek(idx.json.dates[0].file)) requestDay(idx.json.dates[0].file);
  const latest = latestDayJson();
  return performanceView({
    perf: entry.json, busy: state.busy, banners: statusBanners([entry]),
    adoptedStrategy: latest ? latest.strategy : null,
    dayModel: latest ? latest.model : null,
    tab: session.perfTab,
    customText: isCustomSettings() ? describeSettings(getSettings()) + (getSettings().stake !== 100 ? ` · 1点${getSettings().stake}円` : '') : '',
  });
}

/** 今日の推奨の件数(S6 の要約): 今の設定と既定の設定で数える */
function settingsPreview() {
  const idx = peek('index.json');
  if (!idx) {
    if (!state.errors.has('index.json')) requestIndex();
    return { preview: null, date: null };
  }
  const today = todayJst();
  const info = idx.json.dates.find((d) => d.date === today) || idx.json.dates[0];
  if (!info) return { preview: null, date: null };
  const entry = peek(info.file);
  if (!entry) {
    if (!state.errors.has(info.file)) requestDay(info.file);
    return { preview: null, date: info.date };
  }
  let races = 0; let bets = 0; let defaultRaces = 0; let defaultBets = 0;
  const settings = getSettings();
  entry.json.stadiums.forEach((st) => st.races.forEach((race) => {
    const p = race.prediction;
    if (!p || race.state === 'canceled') return;
    const mine = computeRecommendation({ confidence: p.confidence, picks: p.picks }, settings, { manual: {} }).bets.length;
    const def = (p.recommended || []).length;
    if (mine) { races += 1; bets += mine; }
    if (def) { defaultRaces += 1; defaultBets += def; }
  }));
  return { preview: { races, bets, defaultRaces, defaultBets }, date: info.date };
}

function renderSettings(route) {
  const { preview, date } = settingsPreview();
  if (!state.settingsResetNotice) state.settingsResetNotice = takeResetNotice();
  // 401 を受けたあと S2 の「設定へ」から来たとき(合言葉の欄を赤くする)
  const badKey = state.lastUnauthorizedAt || null;
  return settingsView({ preview, date, ui: session.settingsUi, resetNotice: state.settingsResetNotice, badKey });
}

// ---------------------------------------------------------------- 端末内のデータ(S8・S9・印とメモ)

const local = { loaded: false, bets: [], marks: {}, notes: {}, budget: normalizeBudget(null), error: null };
const ledgerUi = { form: {}, formError: '', showAll: false, backupMsg: '', formOpen: false, budgetOpen: false };

async function loadLocal() {
  try {
    const [bets, marks, notes, budget] = await Promise.all(['bets', 'marks', 'notes', 'budget'].map((s) => localdb.getAll(s)));
    local.bets = bets;
    local.marks = Object.fromEntries(marks.map((m) => [m.race_id, normalizeMarks(m.marks)]));
    local.notes = Object.fromEntries(notes.map((n) => [n.race_id, n.text]));
    local.budget = normalizeBudget(budget.find((b) => b.key === 'settings'));
  } catch (e) {
    local.error = String(e && e.message ? e.message : e);
  }
  local.loaded = true;
  render();
}

/** race_id → race(読み込み済みの日だけ。無ければ読み込みを始めて null) */
function raceById(raceId) {
  const parsed = parseRaceId(raceId);
  if (!parsed) return null;
  const idx = peek('index.json');
  if (!idx) { if (!state.errors.has('index.json')) requestIndex(); return null; }
  const info = idx.json.dates.find((d) => d.date === parsed.date);
  if (!info) return null;
  const entry = peek(info.file);
  if (!entry) { if (!state.errors.has(info.file)) requestDay(info.file); return null; }
  const found = findRace(entry.json, raceId);
  return found ? found.race : null;
}
function raceNameOf(raceId) {
  const parsed = parseRaceId(raceId);
  const idx = peek('index.json');
  const info = parsed && idx && idx.json.dates.find((d) => d.date === parsed.date);
  const entry = info && peek(info.file);
  const found = entry && findRace(entry.json, raceId);
  return found ? `${fmtMonthDay(parsed.date)} ${found.stadium.stadium_name} ${parsed.raceNumber}R` : raceId;
}

const BACKUP_KEY = 'br.backup_at';
function backupAt() { try { return localStorage.getItem(BACKUP_KEY); } catch (e) { return null; } }

function renderLedger() {
  if (!local.loaded) return html`<header class="appbar"><h1 class="appbar__title is-pad">収支</h1></header><span class="sr-only" role="status">読み込み中</span>`;
  const s = summarize(local.bets, raceById);
  const monthOf = (iso) => jstDateString(parseIso(iso)).slice(0, 7);
  const today = todayJst();
  const daySpent = local.bets.filter((b) => jstDateString(parseIso(b.bought_at)) === today).reduce((a, b) => a + b.amount, 0);
  const monthSpent = local.bets.filter((b) => monthOf(b.bought_at) === today.slice(0, 7)).reduce((a, b) => a + b.amount, 0);
  const warnings = budgetWarnings(local.budget, { daySpent, monthSpent, currentStreak: s.currentStreak });
  const last = backupAt();
  const hasData = local.bets.length || Object.keys(local.marks).length || Object.keys(local.notes).length;
  const backupStale = !!hasData && (!last || Date.now() - Date.parse(last) > 30 * 86400000);
  const dayMap = new Map();
  s.rows.forEach((row) => { if (row.status === 'pending') return; const d = jstDateString(parseIso(row.bet.bought_at)); dayMap.set(d, (dayMap.get(d) || 0) + row.payout - row.bet.amount); });
  const daily = [...dayMap.entries()].sort().map(([d, v]) => ({ label: fmtMonthDay(d), pnl: v }));
  return ledgerView({ s, daily, spent: { day: daySpent, month: monthSpent }, months: byMonth(s.rows, monthOf), warnings, budget: local.budget, ui: ledgerUi, raceName: raceNameOf, busy: state.busy, dbError: local.error, backupAt: last, backupStale });
}

function renderMarks() {
  if (!local.loaded) return html`<header class="appbar"><h1 class="appbar__title is-pad">自分の予想の成績</h1></header>`;
  const ids = Object.keys(local.marks).filter((id) => Object.keys(local.marks[id]).length);
  const items = ids.map((id) => ({ marks: local.marks[id], race: raceById(id) })).filter((x) => x.race);
  const days = new Set(items.map((x) => x.race.race_id.slice(0, 8)));
  return marksView({ st: markStats(items), total: ids.length, loadedDays: days.size, busy: state.busy });
}

async function saveMark(raceId, entry, mark) {
  const cur = { ...(local.marks[raceId] || {}) };
  if (cur[entry] === mark) delete cur[entry];
  else {
    Object.keys(cur).forEach((k) => { if (cur[k] === mark) delete cur[k]; });
    cur[entry] = mark;
  }
  local.marks[raceId] = normalizeMarks(cur);
  render();
  try { await localdb.put('marks', { race_id: raceId, marks: local.marks[raceId], updated_at: new Date().toISOString() }); } catch (e) { local.error = String(e.message || e); render(); }
}

async function addBets(list) {
  for (const b of list) {
    local.bets.push(b);
    try { await localdb.put('bets', b); } catch (e) { local.error = String(e.message || e); }
  }
  render();
}

function renderAbout() {
  const idx = peek('index.json');
  if (!idx && !state.errors.has('index.json')) requestIndex();
  const latest = latestDayJson();
  if (idx && idx.json.dates.length && !latest) requestDay(idx.json.dates[0].file);
  return aboutView({ index: idx ? idx.json : null, dayModel: latest ? latest.model : null, banners: idx ? statusBanners([idx]) : '' });
}

// ---------------------------------------------------------------- 描画

/** 描き直す前に、いまの details の開閉を覚える(toggle イベントより先に描き直すことがあるため) */
function captureFolds() {
  appEl.querySelectorAll('details[data-fold]').forEach((d) => {
    if (d.dataset.fold === 'players') setPref('playersOpen', d.open);
    else if (d.dataset.race) session.folds[`${d.dataset.race}:${d.dataset.fold}`] = d.open;
  });
}

function render() {
  captureFolds();
  const route = parseRoute();
  if (route.name === 'unknown') {
    history.replaceState(null, '', '#/');
    render();
    return;
  }
  let view;
  try {
    if (route.name === 'home') view = renderHome(route);
    else if (route.name === 'race') view = renderRace(route);
    else if (route.name === 'performance') view = renderPerformance();
    else if (route.name === 'settings') view = renderSettings(route);
    else if (route.name === 'ledger') view = renderLedger();
    else if (route.name === 'marks') view = renderMarks();
    else if (route.name === 'models') view = renderModels();
    else if (route.name === 'analytics') view = route.sub === 'you' ? renderAnalysis() : renderAnalyticsPlaceholder(route.sub);
    else view = renderAbout();
  } catch (e) {
    console.error(e);
    view = html`<header class="appbar"><span class="appbar__title is-pad">ボートレース予想</span></header>${errorBox(e)}`;
  }
  appEl.className = route.name === 'race' ? 'app app--detail' : 'app';
  appEl.innerHTML = toHtmlString(view);
  document.title = titleFor(route);
  afterRender(route);
  renderSheet();
}

function titleFor(route) {
  const base = 'ボートレース予想';
  if (route.name === 'performance') return `成績 - ${base}`;
  if (route.name === 'about') return `その他 - ${base}`;
  if (route.name === 'settings') return `設定 - ${base}`;
  if (route.name === 'ledger') return `収支 - ${base}`;
  if (route.name === 'analytics') return `${t('ana.tab')} - ${base}`;
  if (route.name === 'models') return `${t('models.heading')} - ${base}`;
  if (route.name === 'marks') return `自分の予想の成績 - ${base}`;
  if (route.name === 'race') {
    const h1 = appEl.querySelector('.appbar__title');
    return h1 ? `${h1.textContent} - ${base}` : base;
  }
  return base;
}

/** 描画のあと: スクロール位置、場へのスクロール、ヘッダの監視 */
function afterRender(route) {
  const hasContent = !appEl.querySelector('[aria-busy="true"]');
  if (session.scrollToStadium && route.name === 'home' && hasContent) {
    const el = document.getElementById(`st-${session.scrollToStadium}`);
    session.scrollToStadium = null;
    if (el) {
      el.scrollIntoView({ block: 'start' });
      state.restoreScroll = null;
    }
  }
  if (state.restoreScroll !== null && hasContent) {
    window.scrollTo(0, state.restoreScroll);
    state.restoreScroll = null;
  }
  updateHeadSwap();
  // ローカル表示の目印を、全画面のアプリバーに出す
  if (isLocalData()) {
    const bar = appEl.querySelector('.appbar');
    if (bar && !bar.querySelector('.localmark')) {
      const anchor = bar.querySelector('.appbar__spacer, .appbar__updated, .iconbtn[data-action="reload"]');
      const tmp = document.createElement('span');
      tmp.innerHTML = toHtmlString(localMark());
      bar.insertBefore(tmp.firstElementChild, anchor || null);
    }
  }
  insertGentle();
  insertStale();
  // S2: 自動取得(設定がオンで条件を満たすとき)
  if (route.name === 'race') maybeAutoFetch(route);
  if (route.name === 'settings' && route.focus === 'worker' && !state.workerFocused) {
    state.workerFocused = true;
    const card = document.getElementById('worker-card');
    if (card) card.scrollIntoView({ block: 'start' });
  }
}

/** 表示中のレース(S2)の day と race */
function currentRace(route) {
  const parsed = parseRaceId(route.raceId);
  if (!parsed) return null;
  const entry = peek(dayFileFor(parsed.date));
  if (!entry) return null;
  const found = findRace(entry.json, route.raceId);
  return found ? { ...found, day: entry.json } : null;
}

const autoTried = new Set();
function maybeAutoFetch(route) {
  if (autoTried.has(route.raceId)) return;
  const cur = currentRace(route);
  if (!cur || !cur.race.prediction) return;
  autoTried.add(route.raceId);
  if (shouldAutoFetch(cur.race, route.raceId)) startFetch(cur.race, getSettings().bet_types);
}

/** 取得パネルだけを描き直す(取得中の経過秒・待ち時間を毎秒更新。画面全体は1分ごと) */
function updateFetchPanel() {
  const route = parseRoute();
  if (route.name !== 'race') return;
  const el = appEl.querySelector('[data-fetchpanel]');
  if (!el) return;
  const st = oddsState(route.raceId);
  if (!st.loading && !(st.waitMs > 0)) {
    if (el.dataset.ticking) { delete el.dataset.ticking; render(); }
    return;
  }
  const cur = currentRace(route);
  if (!cur) return;
  const next = fetchPanel(cur.race, raceRecommendation(cur.race, cur.day), cur.day);
  if (!next) return;
  const tmp = document.createElement('div');
  tmp.innerHTML = toHtmlString(next);
  const node = tmp.firstElementChild;
  node.dataset.ticking = '1';
  el.replaceWith(node);
}
setInterval(updateFetchPanel, 1000);
onOddsChange(() => {
  const st = parseRoute().name === 'race' ? oddsState(parseRoute().raceId) : null;
  if (st && st.error && st.error.kind === 'unauthorized') state.lastUnauthorizedAt = st.error.at;
  if (st && st.result && !st.error) state.lastUnauthorizedAt = null;
  render();
});

/** 保存の知らせ(1.5秒) */
let toastTimer = null;
function showToast(text, undo) {
  const root = document.getElementById('toast-root');
  if (!root) return;
  root.innerHTML = toHtmlString(html`<div class="toast" role="status">${text}${undo ? html`<button class="btn btn--text" data-action="settings-undo">元に戻す</button>` : ''}</div>`);
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { root.innerHTML = ''; }, undo ? 4000 : 1500);
}

/** S2: ヘッダがアプリバーに隠れたら、アプリバーの右を残り時間にする */
function updateHeadSwap() {
  const head = appEl.querySelector('[data-racehead]');
  const hidden = !!head && head.getBoundingClientRect().bottom < 56;
  appEl.classList.toggle('is-head-hidden', hidden);
}
window.addEventListener('scroll', updateHeadSwap, { passive: true });

// ---------------------------------------------------------------- S4 日付の選択

let sheetReturnFocus = null;

function renderSheet() {
  if (state.card) {
    const had = !!overlayEl.querySelector('[data-cardsheet]');
    overlayEl.innerHTML = toHtmlString(racerSheet(state.card));
    if (!had) { const el = overlayEl.querySelector('[data-cardsheet]'); if (el) el.focus(); }
    return;
  }
  const idx = peek('index.json');
  if (!state.sheetOpen || !idx) {
    overlayEl.innerHTML = '';
    return;
  }
  const route = parseRoute();
  const today = todayJst();
  const current = route.date || (homeDate(idx.json).date || today);
  overlayEl.innerHTML = toHtmlString(datesSheet({ index: idx.json, currentDate: current, today }));
}

function openSheet(trigger) {
  sheetReturnFocus = trigger || null;
  state.sheetOpen = true;
  renderSheet();
  const sheet = overlayEl.querySelector('[data-sheet]');
  if (sheet) sheet.focus();
}

function closeSheet() {
  if (!state.sheetOpen) return;
  state.sheetOpen = false;
  renderSheet();
  if (sheetReturnFocus && document.body.contains(sheetReturnFocus)) sheetReturnFocus.focus();
  else {
    const btn = appEl.querySelector('[data-action="open-dates"]');
    if (btn) btn.focus();
  }
}

// ---------------------------------------------------------------- 操作

document.addEventListener('click', (e) => {
  const replaceLink = e.target.closest('a[data-replace]');
  if (replaceLink) {
    // 前後Rの移動は履歴に積まない(戻るで一覧へ直接戻れるように)
    e.preventDefault();
    location.replace(replaceLink.getAttribute('href'));
    return;
  }
  const el = e.target.closest('[data-action]');
  if (!el) return;
  const action = el.dataset.action;
  const route = parseRoute();
  switch (action) {
    case 'reload':
      e.preventDefault();
      refresh();
      break;
    case 'reload-app':
      e.preventDefault();
      reloadApp();
      break;
    case 'open-dates':
      e.preventDefault();
      openSheet(el);
      break;
    case 'close-sheet':
      e.preventDefault();
      closeSheet();
      break;
    case 'pick-date':
      // リンクの移動はそのまま。シートは閉じる
      state.sheetOpen = false;
      if (el.getAttribute('href') === (location.hash || '#/')) {
        e.preventDefault();
        renderSheet();
      }
      break;
    case 'set-mode': {
      const idx = peek('index.json');
      const date = route.date || (idx ? homeDate(idx.json).date : null);
      setPref(date === todayJst() ? 'viewToday' : 'viewPast', el.dataset.mode);
      render();
      break;
    }
    case 'toggle-closed': {
      const idx = peek('index.json');
      const date = route.date || (idx ? homeDate(idx.json).date : null);
      const expanded = el.getAttribute('aria-expanded') === 'true';
      session.closedOpen[date] = !expanded;
      render();
      break;
    }
    case 'rank-tab':
      setPref('rankTab', el.dataset.tab);
      render();
      break;
    case 'rank-more':
      session.rankMore[el.dataset.race] = !session.rankMore[el.dataset.race];
      render();
      break;
    case 'goto-stadium': {
      // 前後Rバーの場名: S1 の場ごと表示で、その場までスクロールする
      const parsed = parseRaceId(route.raceId);
      if (parsed) setPref(parsed.date === todayJst() ? 'viewToday' : 'viewPast', 'stadium');
      session.scrollToStadium = el.dataset.stadium;
      break;
    }
    case 'toggle-filter': {
      const d = getDisplay();
      d.filters[el.dataset.filter] = !d.filters[el.dataset.filter];
      saveDisplay(d);
      render();
      break;
    }
    case 'odds-fetch': {
      const cur = currentRace(route);
      if (cur) startFetch(cur.race, getSettings().bet_types);
      break;
    }
    case 'odds-cancel':
      cancelFetch(el.dataset.race);
      break;
    case 'manual-mode': {
      session.manualMode[el.dataset.race] = !session.manualMode[el.dataset.race];
      render();
      const input = session.manualMode[el.dataset.race] && appEl.querySelector('[data-manual-input]');
      if (input) input.focus();
      break;
    }
    case 'set-mark':
      saveMark(el.dataset.race, el.dataset.entry, el.dataset.mark);
      break;
    case 'betsel': {
      const id = el.dataset.race;
      const list = new Set(session.betUnselected[id] || []);
      if (list.has(el.dataset.key)) list.delete(el.dataset.key); else list.add(el.dataset.key);
      session.betUnselected[id] = [...list];
      render();
      break;
    }
    case 'copy-bets': {
      const cur = currentRace(route);
      if (!cur) break;
      const rec = raceRecommendation(cur.race, cur.day);
      const un = new Set(session.betUnselected[cur.race.race_id] || []);
      const sel = rec.bets.filter((b) => !un.has(`${b.bet_type}:${b.combination}`));
      const shown = currentAllocation({ ...rec, bets: sel }, new Set(), getAlloc());
      const text = copyText({ title: raceNameOf(cur.race.race_id), bets: shown.map((x) => ({ label: betTypeLabel(x.bet.bet_type), combination: x.bet.combination, amount: x.amount })), footer: t('notice.copy_footer') });
      const done = () => showToast('買い目をコピーしました');
      if (navigator.clipboard && window.isSecureContext) navigator.clipboard.writeText(text).then(done, () => fallbackCopy(text, done));
      else fallbackCopy(text, done);
      break;
    }
    case 'alloc-method': {
      const a = getAlloc();
      saveAlloc({ ...a, method: el.dataset.value });
      render();
      break;
    }
    case 'alloc-budget': {
      const a = getAlloc();
      const cur = currentRace(route);
      const n = cur ? raceRecommendation(cur.race, cur.day).bets.filter((b) => !(session.betUnselected[cur.race.race_id] || []).includes(`${b.bet_type}:${b.combination}`)).length : 1;
      const base = a.budget === null ? n * 100 : a.budget;
      saveAlloc({ ...a, budget: Math.min(100000, Math.max(100, base + Number(el.dataset.delta))) });
      render();
      break;
    }
    case 'alloc-budget-reset':
      saveAlloc({ ...getAlloc(), budget: null });
      render();
      break;
    case 'card-open':
      openCard(el, route);
      break;
    case 'card-close':
      closeCard();
      break;
    case 'ana-stadium': anaUi.stadium = Number(el.dataset.value); anaUi.cube = {}; render(); break;
    case 'ana-period': anaUi.period = el.dataset.value; render(); break;
    case 'ana-cube': { const [d, v] = el.dataset.value.split(':'); anaUi.cube = { ...anaUi.cube, [d]: v }; render(); break; }
    case 'ana-rkind': anaUi.rkind = el.dataset.value; render(); break;
    case 'ana-rscope': anaUi.rscope = Number(el.dataset.value); render(); break;
    case 'ana-mver': anaUi.mver = el.dataset.value; render(); break;
    case 'ana-week': anaUi.week = el.dataset.value; render(); break;
    case 'ana-simsrc': anaUi.simsrc = el.dataset.value; render(); break;
    case 'ana-simsort': anaUi.simsort = el.dataset.value; render(); break;
    case 'help':
      toggleHelp(el);
      break;
    case 'help-close':
      closeHelp();
      break;
    case 'stale-close':
      // 閉じても、次に開いたときにまだ古ければまた出す(このページを開いている間だけ閉じる)
      stale.dismissedFor = el.dataset.last;
      render();
      break;
    case 'gentle-close':
      dismissGentle(el.dataset.kind);
      break;
    case 'focus-stadium':
      session.focusStadium = Number(el.dataset.stadium);
      render();
      break;
    case 'perf-tab':
      session.perfTab = el.dataset.tab;
      render();
      break;
    case 'record-recs': {
      const cur = currentRace(route);
      if (!cur) break;
      const recAll = raceRecommendation(cur.race, cur.day);
      const unsel = new Set(session.betUnselected[cur.race.race_id] || []);
      const rec = { ...recAll, bets: recAll.bets.filter((b) => !unsel.has(`${b.bet_type}:${b.combination}`)) };
      const now = new Date().toISOString();
      // 表示中の金額で記録する(0円の買い目は記録しない)
      const shown = currentAllocation(rec, new Set(), getAlloc());
      // 分析用に、記録した時点の期待値ランクと配分のしかたも残す(spec/11 2.5)
      const method = getAlloc().method;
      addBets(shown.map((x) => ({ id: localdb.newId(), race_id: cur.race.race_id, bet_type: x.bet.bet_type, combination: x.bet.combination, amount: x.amount, bought_at: now, source: 'model', memo: '', ev_rank: x.bet.ev_rank || null, allocation: method })));
      showToast(`${shown.length}点を記録しました(収支で確認・削除できます)`);
      break;
    }
    case 'ledger-add': {
      const f = new FormData(el.closest('form'));
      const race_id = String(f.get('race_id') || '').trim();
      const bet_type = String(f.get('bet_type'));
      const combination = String(f.get('combination') || '').trim().replace(/[ー－]/g, '-');
      const amount = Number(String(f.get('amount') || '').trim());
      const n = { win: 1, exacta: 2, trifecta: 3 }[bet_type];
      const parts = combination.split('-');
      ledgerUi.form = { race_id, bet_type, combination, amount: f.get('amount') };
      if (!parseRaceId(race_id)) ledgerUi.formError = 'レースは 20261102-02-05 の形で入れてください';
      else if (parts.length !== n || !parts.every((x) => /^[1-6]$/.test(x)) || new Set(parts).size !== n) ledgerUi.formError = `${n}艇の枠番を「-」でつないでください`;
      else if (!Number.isInteger(amount) || amount < 100 || amount % 100) ledgerUi.formError = '金額は100円単位で入れてください';
      else {
        ledgerUi.formError = '';
        ledgerUi.form = { bet_type };
        addBets([{ id: localdb.newId(), race_id, bet_type, combination, amount, bought_at: new Date().toISOString(), source: 'self', memo: '', allocation: 'manual' }]);
        showToast('記録しました');
        break;
      }
      render();
      break;
    }
    case 'ledger-delete':
      if (window.confirm('この記録を消しますか?')) {
        local.bets = local.bets.filter((b) => b.id !== el.dataset.id);
        localdb.remove('bets', el.dataset.id).catch((e2) => { local.error = String(e2.message || e2); });
        render();
      }
      break;
    case 'ledger-toggle':
      e.preventDefault();
      if (el.dataset.what === 'form') ledgerUi.formOpen = !ledgerUi.formOpen;
      else ledgerUi.budgetOpen = !ledgerUi.budgetOpen;
      render();
      break;
    case 'ledger-all':
      ledgerUi.showAll = !ledgerUi.showAll;
      render();
      break;
    case 'backup-export':
      localdb.exportAll().then((data) => {
        const blob = new Blob([JSON.stringify(data, null, 1)], { type: 'application/json' });
        const a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = `br-yoso-backup-${todayJst()}.json`;
        document.body.appendChild(a);
        a.click();
        a.remove();
        setTimeout(() => URL.revokeObjectURL(a.href), 1000);
        try { localStorage.setItem('br.backup_at', new Date().toISOString()); } catch (e2) { /* 無視 */ }
        ledgerUi.backupMsg = `書き出しました(購入記録 ${data.bets.length}件・印 ${data.marks.length}件・メモ ${data.notes.length}件)`;
        render();
      }).catch((e2) => { ledgerUi.backupMsg = `書き出せませんでした: ${e2.message}`; render(); });
      break;
    case 'manual-clear':
      clearManualOdds(el.dataset.race);
      render();
      break;
    case 'settings-back':
      // 来た画面へ戻る(履歴が無ければ「その他」)
      if (state.cameFromApp) {
        e.preventDefault();
        history.back();
      }
      break;
    default:
      handleSettingsAction(action, el, e);
      break;
  }
});

/** S6 の操作 */
let undoSettings = null;
function handleSettingsAction(action, el) {
  const s = JSON.parse(JSON.stringify(getSettings()));
  const before = JSON.parse(JSON.stringify(s));
  let changed = true;
  switch (action) {
    case 'set-bettype': {
      const bt = el.dataset.bt;
      if (bt in s.bet_types) {
        if (Object.keys(s.bet_types).length === 1) return; // 最後の1つは外せない
        delete s.bet_types[bt];
      } else {
        s.bet_types[bt] = bt === 'trifecta' ? 5 : CONSTANTS.k_ranges[bt][0] + (bt === 'exacta' ? 2 : 0);
      }
      break;
    }
    case 'set-k': {
      const bt = el.dataset.bt;
      const [lo, hi] = CONSTANTS.k_ranges[bt];
      s.bet_types[bt] = Math.max(lo, Math.min(hi, s.bet_types[bt] + Number(el.dataset.delta)));
      break;
    }
    case 'set-minprob':
      s.min_prob = el.dataset.value === 'null' ? null : Number(el.dataset.value);
      break;
    case 'set-evfilter':
      s.ev_filter = el.dataset.value;
      break;
    case 'set-noodds':
      s.no_odds = el.dataset.value;
      break;
    case 'set-skip':
      s.skip_confidence = el.dataset.value;
      break;
    case 'set-stake': {
      const [lo, hi] = CONSTANTS.stake_range;
      s.stake = Math.max(lo, Math.min(hi, s.stake + Number(el.dataset.delta)));
      break;
    }
    case 'settings-reset':
      undoSettings = before;
      resetSettings();
      render();
      showToast('既定に戻しました', true);
      return;
    case 'settings-undo':
      if (undoSettings) saveSettings(undoSettings);
      undoSettings = null;
      render();
      showToast('元に戻しました');
      return;
    case 'set-theme': {
      const d = getDisplay();
      d.theme = el.dataset.value;
      saveDisplay(d);
      render();
      showToast('保存しました');
      return;
    }
    case 'toggle-fav': {
      const d = getDisplay();
      const n = Number(el.dataset.value);
      d.favorites = d.favorites.includes(n) ? d.favorites.filter((x) => x !== n) : d.favorites.concat(n).sort((a, b) => a - b);
      saveDisplay(d);
      render();
      showToast('保存しました');
      return;
    }
    case 'toggle-ranktype': {
      const d = getDisplay();
      const v = el.dataset.value;
      if (d.rank_types.includes(v)) {
        if (d.rank_types.length === 1) return;
        d.rank_types = d.rank_types.filter((x) => x !== v);
      } else d.rank_types = CONSTANTS.bet_type_order.filter((x) => x === v || d.rank_types.includes(x));
      saveDisplay(d);
      render();
      showToast('保存しました');
      return;
    }
    case 'worker-key-edit':
      session.settingsUi.editingKey = true;
      render();
      { const input = appEl.querySelector('[data-worker-key]'); if (input) input.focus(); }
      return;
    case 'worker-key-toggle':
      session.settingsUi.showKey = !session.settingsUi.showKey;
      { const input = appEl.querySelector('[data-worker-key]'); if (input) input.type = session.settingsUi.showKey ? 'text' : 'password'; }
      el.setAttribute('aria-pressed', String(session.settingsUi.showKey));
      el.textContent = session.settingsUi.showKey ? '隠す' : '表示';
      return;
    case 'worker-auto': {
      const cfg = getWorkerConfig();
      if (!cfg.ready) return;
      saveWorkerConfig({ auto: !cfg.auto });
      render();
      showToast('保存しました');
      return;
    }
    default:
      changed = false;
  }
  if (!changed) return;
  saveSettings(s);
  render();
  showToast('保存しました');
}

// 入力欄: Worker の URL・合言葉、手入力のオッズ(change = 欄から離れたとき・Enter)
document.addEventListener('input', (e) => {
  const t = e.target;
  if (t.matches && t.matches('[data-worker-url]')) {
    session.settingsUi.urlDraft = t.value;
    t.closest('.field').classList.toggle('is-invalid', !validWorkerUrl(t.value.trim()));
  }
});
document.addEventListener('change', (e) => {
  const t = e.target;
  if (!t.matches) return;
  if (t.matches('[data-worker-url]')) {
    const v = t.value.trim();
    if (!validWorkerUrl(v)) return;
    saveWorkerConfig({ url: v });
    session.settingsUi.urlDraft = undefined;
    render();
    showToast('保存しました');
  } else if (t.matches('[data-worker-key]')) {
    // 合言葉は保存したら画面に戻さない(入力欄も消す)
    const v = t.value;
    t.value = '';
    if (!v) return;
    saveWorkerConfig({ key: v });
    session.settingsUi.editingKey = false;
    session.settingsUi.showKey = false;
    state.lastUnauthorizedAt = null;
    render();
    showToast('保存しました');
  } else if (t.matches('[data-memo]')) {
    const text = t.value.slice(0, 500);
    local.notes[t.dataset.race] = text;
    (text ? localdb.put('notes', { race_id: t.dataset.race, text, updated_at: new Date().toISOString() }) : localdb.remove('notes', t.dataset.race))
      .then(() => showToast('メモを保存しました')).catch((e2) => { local.error = String(e2.message || e2); });
  } else if (t.matches('[data-budget-form] input')) {
    const f = new FormData(t.closest('form'));
    const num = (v) => { const x = Number(String(v || '').trim()); return String(v || '').trim() === '' ? null : x; };
    local.budget = normalizeBudget({ day_limit: num(f.get('day_limit')), month_limit: num(f.get('month_limit')), streak_warn: num(f.get('streak_warn')) ?? 0 });
    localdb.put('budget', local.budget).then(() => { render(); showToast('保存しました'); }).catch((e2) => { local.error = String(e2.message || e2); render(); });
  } else if (t.matches('[data-backup-import]')) {
    const file = t.files && t.files[0];
    if (!file) return;
    file.text().then((text) => localdb.importAll(JSON.parse(text))).then((c) => {
      ledgerUi.backupMsg = `読み込みました(購入記録 ${c.bets}件・印 ${c.marks}件・メモ ${c.notes}件。同じ記録は上書き)`;
      return loadLocal();
    }).catch((e2) => { ledgerUi.backupMsg = `読み込めませんでした: ${e2.message}`; render(); });
  } else if (t.matches('[data-manual-input]')) {
    const raw = t.value.trim().replace(/[０-９．]/g, (c) => String.fromCharCode(c.charCodeAt(0) - 0xfee0));
    if (raw === '') {
      setManualOdds(t.dataset.race, t.dataset.bt, t.dataset.comb, null);
    } else {
      const n = Number(raw);
      if (!Number.isFinite(n) || n < 1 || n > 99999) {
        t.setAttribute('aria-invalid', 'true');
        return;
      }
      // 手入力は小数1桁にそろえる(spec/05 S7)
      setManualOdds(t.dataset.race, t.dataset.bt, t.dataset.comb, Math.round(n * 10) / 10);
    }
    // 次の欄へ移ったときは、描き直したあとも同じ欄に戻す
    setTimeout(() => {
      const a = document.activeElement;
      const next = a && a.matches && a.matches('[data-manual-input]') ? `[data-manual-input][data-bt="${a.dataset.bt}"][data-comb="${a.dataset.comb}"]` : null;
      render();
      if (next) {
        const el = appEl.querySelector(next);
        if (el) el.focus();
      }
    }, 0);
  }
});
document.addEventListener('keydown', (e) => {
  const t = e.target;
  if (e.key === 'Enter' && t.matches && (t.matches('[data-manual-input]') || t.matches('[data-worker-url]') || t.matches('[data-worker-key]'))) {
    t.blur();
  }
});

// details の開閉を覚えておく(描き直しても保つ)
document.addEventListener('toggle', (e) => {
  const d = e.target;
  if (!(d instanceof HTMLDetailsElement) || !d.dataset.fold) return;
  if (d.dataset.fold === 'players') setPref('playersOpen', d.open);
  else if (d.dataset.race) session.folds[`${d.dataset.race}:${d.dataset.fold}`] = d.open;
}, true);

document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') { closeHelp(); if (state.card) closeCard(); }
  if (e.key === 'Escape' && state.sheetOpen) closeSheet();
});

// 左右スワイプで前後R(水平 > 60px かつ 垂直の1.5倍以上。表の横スクロール・タブの上は除く)
let touchStart = null;
document.addEventListener('touchstart', (e) => {
  if (parseRoute().name !== 'race' || e.touches.length !== 1) {
    touchStart = null;
    return;
  }
  if (e.target.closest('[data-noswipe], .tabs, .sheet, .racenav')) {
    touchStart = null;
    return;
  }
  touchStart = { x: e.touches[0].clientX, y: e.touches[0].clientY };
}, { passive: true });
document.addEventListener('touchend', (e) => {
  if (!touchStart) return;
  const t = e.changedTouches[0];
  const dx = t.clientX - touchStart.x;
  const dy = t.clientY - touchStart.y;
  touchStart = null;
  if (Math.abs(dx) <= 60 || Math.abs(dx) < Math.abs(dy) * 1.5) return;
  const link = appEl.querySelector(dx < 0 ? '.racenav a.next' : '.racenav a.prev');
  if (link) location.replace(link.getAttribute('href'));
}, { passive: true });

// S3 推移グラフ: タップした位置の日付・その日の回収率・累積回収率を出す
function showChartTip(e) {
  const svg = e.target.closest && e.target.closest('svg[data-chart]');
  if (!svg) return;
  const entry = peek('performance.json');
  const byDay = entry && entry.json.live && entry.json.live.by_day;
  if (!byDay || !byDay.length) return;
  const { geometry } = chartSvg(byDay);
  const rect = svg.getBoundingClientRect();
  const vx = ((e.clientX - rect.left) / rect.width) * geometry.width;
  const span = geometry.right - geometry.left;
  const i = byDay.length === 1 ? 0 : Math.max(0, Math.min(byDay.length - 1, Math.round(((vx - geometry.left) / span) * (byDay.length - 1))));
  const px = byDay.length === 1 ? (geometry.left + geometry.right) / 2 : geometry.left + (span * i) / (byDay.length - 1);
  const cursor = svg.querySelector('[data-cursor]');
  cursor.setAttribute('x1', px);
  cursor.setAttribute('x2', px);
  cursor.setAttribute('visibility', 'visible');
  const tip = svg.parentElement.querySelector('[data-chart-tip]');
  const d = byDay[i];
  tip.innerHTML = toHtmlString(html`${fmtMonthDay(d.date)} <b>累積 ${fmtRoi(d.cum_roi)}</b> · 日別 ${fmtRoi(d.roi)}`);
  tip.hidden = false;
  const left = Math.max(70, Math.min(rect.width - 70, (px / geometry.width) * rect.width));
  tip.style.left = `${left}px`;
}
document.addEventListener('pointerdown', showChartTip);
document.addEventListener('pointermove', (e) => {
  if (e.pointerType === 'mouse' || e.buttons) showChartTip(e);
});

// ---------------------------------------------------------------- 画面の切り替え・時刻の更新

window.addEventListener('hashchange', () => {
  state.cameFromApp = true;
  state.card = null;
  if (parseRoute().name !== 'settings') {
    state.settingsResetNotice = false;
    state.workerFocused = false;
  }
  // 離れる画面のスクロール位置を覚え、戻ってきたときに戻す(S1 のみ)
  const route = parseRoute();
  state.sheetOpen = false;
  if (route.name === 'home' && session.scroll.has(location.hash || '#/')) {
    state.restoreScroll = session.scroll.get(location.hash || '#/');
  } else {
    state.restoreScroll = 0;
  }
  render();
  state.lastHash = location.hash || '#/';
});

// スクロールのたびに現在の画面の位置を覚える
window.addEventListener('scroll', () => {
  session.scroll.set(location.hash || '#/', window.scrollY);
}, { passive: true });

// 残り時間は分の切り替わりに合わせて更新する(データは取り直さない)
function tick() {
  if (document.visibilityState !== 'visible') return;
  const name = parseRoute().name;
  if (name !== 'home' && name !== 'race') return;
  const y = window.scrollY;
  render();
  window.scrollTo(0, y);
}
function scheduleTicks() {
  const msToNext = 60000 - (Date.now() % 60000) + 50;
  setTimeout(() => {
    tick();
    setInterval(tick, 60000);
  }, msToNext);
}

// 前面に戻ったとき、前回の取得から5分以上たっていれば取り直す(定期ポーリングはしない)
const REFRESH_AFTER_MS = 5 * 60 * 1000;
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState !== 'visible') return;
  const last = lastFetchedAt();
  if (last && Date.now() - last >= REFRESH_AFTER_MS) refresh();
  else tick();
});
// 通信が戻ったら取り直す
window.addEventListener('online', () => refresh());
window.addEventListener('offline', () => render());

// ---------------------------------------------------------------- Service Worker

function registerServiceWorker() {
  if (!('serviceWorker' in navigator)) return;
  // 相対パスで登録する(GitHub Pages のサブパスで動かすため)
  navigator.serviceWorker.register('sw.js').catch((e) => console.warn('Service Worker を登録できませんでした', e));
}

/** アプリの更新(schema_version が新しいとき): Service Worker を更新してから読み込み直す */
async function reloadApp() {
  try {
    const reg = 'serviceWorker' in navigator ? await navigator.serviceWorker.getRegistration() : null;
    if (reg) {
      await reg.update();
      const worker = reg.installing || reg.waiting;
      if (worker) {
        await new Promise((resolve) => {
          const timer = setTimeout(resolve, 4000);
          worker.addEventListener('statechange', () => {
            if (worker.state === 'activated') {
              clearTimeout(timer);
              resolve();
            }
          });
        });
      }
    }
  } catch (e) {
    console.warn(e);
  }
  location.reload();
}

// ---------------------------------------------------------------- 用語説明「?」(B の .help / .helppop)

function closeHelp() {
  document.querySelectorAll('.helppop[data-pop]').forEach((p) => p.remove());
  document.querySelectorAll('.help[aria-expanded="true"]').forEach((b) => b.setAttribute('aria-expanded', 'false'));
}
function toggleHelp(btn) {
  const open = btn.getAttribute('aria-expanded') === 'true';
  closeHelp();
  if (open) return;
  const key = btn.dataset.help;
  const pop = document.createElement('div');
  pop.className = 'helppop';
  pop.dataset.pop = '1';
  pop.setAttribute('role', 'dialog');
  pop.id = `hp-${key.replace(/\W/g, '-')}`;
  pop.innerHTML = toHtmlString(html`<button class="helppop__close" data-action="help-close" aria-label="閉じる">×</button><b class="helppop__title">${helpTitleOf(key)}</b>${WORDING[key] || ''}`);
  const anchor = btn.closest('.card__head, .set-label, .versionbar, .betsum__cell, .betsum__foot, .skip-box, h2, p, div') || btn.parentElement;
  anchor.after(pop);
  btn.setAttribute('aria-expanded', 'true');
  btn.setAttribute('aria-controls', pop.id);
}
function helpTitleOf(key) {
  const b = document.querySelector(`.help[data-help="${key}"]`);
  return b ? b.getAttribute('aria-label').replace(/とは$/, '') : '';
}
// 外側をタップしたら閉じる
document.addEventListener('pointerdown', (e) => {
  if (!e.target.closest || e.target.closest('.helppop, .help')) return;
  closeHelp();
});

function fallbackCopy(text, done) {
  const ta = document.createElement('textarea');
  ta.value = text;
  ta.setAttribute('readonly', '');
  ta.style.position = 'fixed';
  ta.style.opacity = '0';
  document.body.appendChild(ta);
  ta.select();
  try { document.execCommand('copy'); done(); } catch (e) { showToast('コピーできませんでした'); }
  ta.remove();
}

// ---------------------------------------------------------------- 深夜・連続利用の控えめな表示(spec/08 4章-7)

const SESSION_KEY = 'br.session_start';
function sessionStart() {
  try {
    let v = Number(sessionStorage.getItem(SESSION_KEY));
    if (!v) { v = Date.now(); sessionStorage.setItem(SESSION_KEY, String(v)); }
    return v;
  } catch (e) { return Date.now(); }
}
const gentleDismissed = {};
function dismissGentle(kind) {
  gentleDismissed[kind] = kind === 'long' ? Math.floor((Date.now() - sessionStart()) / 3600000) : true;
  render();
}
function gentleBanners() {
  const out = [];
  const hour = new Date(Date.now() + 9 * 3600000).getUTCHours();
  if (hour < 5 && !gentleDismissed.night) out.push(['night', t('warn.late_night')]);
  const hours = Math.floor((Date.now() - sessionStart()) / 3600000);
  if (hours >= 1 && gentleDismissed.long !== hours) out.push(['long', t('warn.session_long', { min: hours * 60 })]);
  return out.map(([kind, text]) => html`<div class="banner banner--quiet" role="status"><span>${text}</span><button class="banner__close" data-action="gentle-close" data-kind="${kind}" aria-label="閉じる">閉じる</button></div>`);
}
function insertGentle() {
  const items = gentleBanners();
  if (!items.length) return;
  const bar = appEl.querySelector('.appbar');
  if (!bar) return;
  const host = document.createElement('div');
  host.className = 'gentle-host';
  host.innerHTML = toHtmlString(items);
  bar.after(host);
}
setInterval(() => { if (gentleBanners().length && !appEl.querySelector('.gentle-host')) render(); }, 60000);

// ---------------------------------------------------------------- 1レースの予算と配分のしかた(端末内に保存。過去の収支は使わない)
const ALLOC_KEY = 'br.alloc.v1';
function getAlloc() {
  try {
    const v = JSON.parse(localStorage.getItem(ALLOC_KEY) || 'null') || {};
    const budget = Number.isInteger(v.budget) && v.budget >= 100 && v.budget <= 100000 && v.budget % 100 === 0 ? v.budget : null;
    return { budget, method: ALLOCATION_METHODS.includes(v.method) ? v.method : 'equal' };
  } catch (e) { return { budget: null, method: 'equal' }; }
}
function saveAlloc(a) {
  try { localStorage.setItem(ALLOC_KEY, JSON.stringify({ budget: a.budget, method: a.method })); } catch (e) { /* 保存できなくても表示は続ける */ }
}

// ---------------------------------------------------------------- 自動更新が止まったときの帯(spec/10 1章、W-14)
// status.json の last_success_at(無ければ index.json の generated_at)から90分以上たっていて、
// 今が当日の開催時間(最初の締切 − 60分 〜 最後の締切 + 60分)の中なら出す。夜間・開催の無い日は出さない。
const STALE_MINUTES = 90;
const stale = { lastSuccessAt: null, checkedAt: 0, dismissedFor: null };

async function loadStatus() {
  try {
    const res = await fetch(`${dataBase()}status.json`, { cache: 'no-cache' });
    const j = res.ok ? await res.json() : null;
    stale.lastSuccessAt = j && typeof j.last_success_at === 'string' ? j.last_success_at : null;
  } catch (e) {
    stale.lastSuccessAt = null;
  }
  stale.checkedAt = Date.now();
  render();
}

/** 帯を出すか。出すときは { last, min } */
function staleState() {
  const idx = peek('index.json');
  const last = stale.lastSuccessAt || (idx && idx.json.generated_at);
  if (!last || !idx) return null;
  const today = todayJst();
  const info = idx.json.dates.find((d) => d.date === today);
  const entry = info && peek(info.file);
  if (!entry) { if (info && !state.errors.has(info.file)) requestDay(info.file); return null; }
  const closes = entry.json.stadiums.flatMap((st) => st.races.map((r) => parseIso(r.closed_at))).filter((x) => !Number.isNaN(x));
  if (!closes.length) return null;
  const now = nowMs();
  if (now < Math.min(...closes) - 3600000 || now > Math.max(...closes) + 3600000) return null;
  const min = Math.floor((now - parseIso(last)) / 60000);
  if (!(min >= STALE_MINUTES)) return null;
  return { last, min };
}

function insertStale() {
  const s = staleState();
  appEl.classList.toggle('has-stale', !!s && stale.dismissedFor !== (s && s.last));
  if (!s || stale.dismissedFor === s.last) return;
  const bar = appEl.querySelector('.appbar');
  if (!bar) return;
  const host = document.createElement('div');
  host.className = 'stale-host';
  host.innerHTML = toHtmlString(html`<div class="stale" role="status"><div class="stale__body"><b>${t('stale.banner', { time: fmtUpdated(s.last, todayJst()) })}</b><span>${t('stale.detail', { min: s.min })}</span></div>
    <button class="stale__close" data-action="stale-close" data-last="${s.last}" aria-label="閉じる">閉じる</button></div>`);
  bar.after(host);
}

// ---------------------------------------------------------------- 選手カード(W-16)・モデルの更新履歴(W-17)

/** 選手カードを開く。racers/YYYYMMDD.json は開いたときに初めて読む。失敗しても S2 は壊さない */
function openCard(el, route) {
  const parsed = parseRaceId(route.raceId);
  if (!parsed) return;
  const cur = currentRace(route);
  const b = cur && cur.race.prediction ? cur.race.prediction.boats.find((x) => String(x.entry) === el.dataset.entry) : null;
  const course = b ? (b.exhibition_course || b.entry) : Number(el.dataset.entry);
  state.cardReturn = el;
  const path = `racers/${parsed.compact}.json`;
  const base = { entry: Number(el.dataset.entry), name: el.dataset.name, state: { status: 'loading', course } };
  state.card = base;
  renderSheet();
  load(path).then((entry) => {
    if (state.card !== base) return;
    const j = entry.json;
    const racer = j.racers ? j.racers[el.dataset.racer] : null;
    state.card = { ...base, state: { status: racer ? 'ok' : 'failed', racer, course, techniques: j.techniques } };
    renderSheet();
  }).catch((err) => {
    if (state.card !== base) return;
    state.card = { ...base, state: { status: err && err.kind === 'notfound' ? 'none' : 'failed', course } };
    renderSheet();
  });
}
function closeCard() {
  if (!state.card) return;
  state.card = null;
  renderSheet();
  if (state.cardReturn && document.body.contains(state.cardReturn)) state.cardReturn.focus();
}
// シートを下へスワイプで閉じる
let cardTouch = null;
document.addEventListener('touchstart', (e) => {
  cardTouch = state.card && e.target.closest && e.target.closest('.sheet__head, .sheet__handle') ? e.touches[0].clientY : null;
}, { passive: true });
document.addEventListener('touchend', (e) => {
  if (cardTouch !== null && e.changedTouches[0].clientY - cardTouch > 80) closeCard();
  cardTouch = null;
}, { passive: true });

const modelsState = { data: null, error: null, loading: false };
function renderModels() {
  if (!modelsState.data && !modelsState.error && !modelsState.loading) {
    modelsState.loading = true;
    load('models.json').then((e) => { modelsState.data = e.json; }).catch((err) => { modelsState.error = describeError(err).text; }).finally(() => { modelsState.loading = false; render(); });
  }
  const bar = html`<header class="appbar"><a class="iconbtn" href="#/about" aria-label="戻る">${ICONS.back}</a><h1 class="appbar__title">${t('models.heading')}</h1></header>`;
  return modelsView({ data: modelsState.data, error: modelsState.error, appbar: bar, tabbar: tabbar('about') });
}

// ---------------------------------------------------------------- あなたの収支分析(W-21)
const STADIUM_NAMES_SHORT = ['桐生', '戸田', '江戸川', '平和島', '多摩川', '浜名湖', '蒲郡', '常滑', '津', '三国', 'びわこ', '住之江', '尼崎', '鳴門', '丸亀', '児島', '宮島', '徳山', '下関', '若松', '芦屋', '福岡', '唐津', '大村'];
function renderAnalysis() {
  if (!local.loaded) return html`<header class="appbar"><h1 class="appbar__title is-pad">あなたの収支分析</h1></header>`;
  const s = summarize(local.bets, raceById);
  const hourOf = (row) => { const r = raceById(row.bet.race_id); const ms = r ? parseIso(r.closed_at) : NaN; return Number.isNaN(ms) ? null : new Date(ms + 9 * 3600000).getUTCHours(); };
  const sections = [
    ['券種別', (row) => betTypeLabel(row.bet.bet_type)],
    ['場別', (row) => { const p = parseRaceId(row.bet.race_id); return p ? STADIUM_NAMES_SHORT[p.stadiumNumber - 1] || '不明' : '不明'; }],
    ['時間帯別(締切時刻)', (row) => timeBand(hourOf(row))],
    ['期待値ランク別(記録したとき)', (row) => evRankKey(row.bet)],
    ['配分のしかた別', (row) => allocationKey(row.bet)],
    ['自分の印', (row) => (local.marks[row.bet.race_id] && Object.keys(local.marks[row.bet.race_id]).length ? '印あり' : '印なし')],
  ].map(([title, keyOf]) => ({ title, rows: groupStats(s.rows, keyOf) }));
  return analysisView({ sections, total: s.races, busy: state.busy, subtabs: analyticsSubtabs('you') });
}

// ---------------------------------------------------------------- 分析タブの骨組み(W-18。中身は W-19・W-20)
const ANALYTICS_TABS = [['stadium', '場'], ['players', '今日の選手'], ['model', 'モデル'], ['sim', '買い方'], ['you', 'あなた']];
function analyticsSubtabs(current) {
  return html`<nav class="subtabs subtabs--sticky subtabs--fill" role="tablist" aria-label="${t('ana.tab')}">${ANALYTICS_TABS.map(([k, name]) => html`<a role="tab" href="#/analytics/${k}" aria-selected="${String(k === current)}" data-replace>${name}</a>`)}</nav>`;
}
const anaUi = { stadium: 0, period: 'all', cube: {}, rkind: null, rscope: 0, mver: 'pre', week: null, simsrc: 'validation', simsort: 'default' };
const anaFiles = {};
function anaLoad(path) {
  if (anaFiles[path] !== undefined) return anaFiles[path] && anaFiles[path].json;
  anaFiles[path] = null;
  load(`analytics/${path}`).then((e) => { anaFiles[path] = e; }).catch(() => { anaFiles[path] = false; }).finally(render);
  return undefined;
}
function renderAnalyticsPlaceholder(sub) {
  const idx = anaLoad('index.json');
  let body;
  if (anaFiles['index.json'] === false) body = html`<div class="emptybox" role="alert">分析のデータを読み込めませんでした。</div>`;
  else if (!idx) body = html`<div class="emptybox" role="status">読み込み中…</div>`;
  else if (sub === 'stadium') {
    const data = anaLoad('stadiums.json');
    const sid = data && Object.keys(data.stadiums).map(Number).includes(anaUi.stadium) ? anaUi.stadium : 0;
    const cube = sid ? anaLoad(`cube/${String(sid).padStart(2, '0')}.json`) : null;
    body = stadiumTab({ data, cube: sid ? cube : { rows: [] }, ui: { ...anaUi, stadium: sid } });
  } else if (sub === 'players') {
    const dates = (idx.files.rankings || []).slice().sort();
    const want = todayJst().replace(/-/g, '');
    const d = dates.includes(want) ? want : dates[dates.length - 1];
    const stad = anaLoad('stadiums.json');
    body = d ? playersTab({ data: anaLoad(`rankings/${d}.json`), ui: anaUi, stadiumName: (n) => (stad && stad.stadiums[n] ? stad.stadiums[n].name : `${n}場`) }) : playersTab({ data: null, ui: anaUi });
  } else if (sub === 'model') {
    const weeks = (idx.files.weekly || []).slice().sort();
    const w = weeks.includes(anaUi.week) ? anaUi.week : weeks[weeks.length - 1];
    body = modelTab({ weak: anaLoad('model_weakness.json'), weekly: w ? anaLoad(`weekly/${w}.json`) : null, weeks, ui: { ...anaUi, week: w } });
  } else body = simTab({ data: anaLoad('simulator.json'), ui: anaUi });
  return html`<header class="appbar"><h1 class="appbar__title is-pad">${t('ana.tab')}</h1></header>
    ${analyticsSubtabs(sub)}
    ${body}
    ${tabbar('analytics')}`;
}

// ---------------------------------------------------------------- 起動

if (isClockFixed()) console.info('現在時刻を URL の now パラメータで固定しています');
state.lastHash = location.hash || '#/';
render();
loadLocal();
loadStatus();
scheduleTicks();
registerServiceWorker();
