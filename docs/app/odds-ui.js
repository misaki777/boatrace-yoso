// 期待値ランク・オッズの出どころ・推奨の計算し直しを、画面(S1・S2)で使う形にまとめる。
// 出どころの優先: 手入力 → Worker → 公開JSONの ev_rank → なし(組合せごと。spec/07 9.6)
import { html, raw, cls } from './html.js';
import { fmtClock, parseIso, todayJst } from './time.js';
import { strategyLabel, betTypeLabel, fmtOdds, fmtYen } from './format.js';
import { computeRecommendation, evaluatePick, isDefaultSelection, CONSTANTS } from './strategy.js';
import { getSettings, isCustomSettings, getManualOdds, getWorkerConfig } from './settings.js';
import { oddsState, workerOddsInput, failedPages, PAGE_LABELS, SECONDS_PER_PAGE, canFetch } from './oddsfetch.js';
import { isLocalData } from './data.js';
import { raceStatus } from './model.js';

/** 締切の何分前に一括取得するか(表示の目安。spec/07 2.3 の既定) */
const SNAPSHOT_MINUTES_BEFORE = 5;

const ICON = {
  clock: raw('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>'),
  warn: raw('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 3l10 18H2z"/><path d="M12 10v5M12 18v.01"/></svg>'),
  stop: raw('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M6 6l12 12"/></svg>'),
  home: raw('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 11l9-7 9 7"/><path d="M5 10v10h14V10"/></svg>'),
  pencil: raw('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 20l4-1 11-11-3-3L5 16z"/></svg>'),
  down: raw('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 4v12M6 11l6 6 6-6"/></svg>'),
};

export const RANK_LEGEND = '期待値ランク: A 1.2以上 · B 1.0〜1.2 · C 0.8〜1.0 · D 0.8未満';
export const ODDS_NOTE = html`<b>締切前のオッズは締切までに変わります。</b>確定オッズ(払戻の倍率)とは違います。買う前に投票アプリの最新オッズで確かめてください。`;

/** 期待値ランクのバッジ(文字 + 形。色だけに頼らない) */
export function evRankBadge(rank, { sm = false } = {}) {
  const r = rank ? String(rank).toLowerCase() : 'none';
  const label = rank ? `期待値ランク${rank}` : '期待値ランクなし';
  return html`<span class="${cls('evrank', `evrank--${r}`, sm && 'evrank--sm')}" role="img" aria-label="${label}">${rank || '–'}</span>`;
}

/** ローカル表示の目印 */
export function localMark() {
  return html`<span class="localmark" title="PC のローカルデータ(オッズ・期待値の数値あり)">${ICON.home}ローカル</span>`;
}

const dayOddsCache = new WeakMap();
/** その日のどこかのレースに odds_snapshot があるか(無ければランクの列も帯も出さない) */
export function dayHasOdds(day) {
  if (dayOddsCache.has(day)) return dayOddsCache.get(day);
  const has = day.stadiums.some((s) => s.races.some((r) => r.prediction && r.prediction.odds_snapshot));
  dayOddsCache.set(day, has);
  return has;
}

/** レースのオッズの入力(手入力・Worker) */
export function oddsInputFor(raceId) {
  const manual = getManualOdds(raceId);
  const worker = workerOddsInput(raceId);
  return { manual, worker: worker || undefined };
}

function countManual(manual) {
  return Object.values(manual || {}).reduce((n, t) => n + Object.keys(t || {}).length, 0);
}

/** 既定の推奨(export の recommended)を、今のオッズで評価し直したもの */
function evaluatedDefaultRecs(prediction, oddsInput) {
  return (prediction.recommended || []).map((b) => ({ ...b, ...evaluatePick(b, b.bet_type, oddsInput), required_odds: b.required_odds, bet_type: b.bet_type }));
}

/**
 * レースの推奨を決める。
 * - 選び方が既定のまま: export の recommended をそのまま使う(spec/07 4.2)
 * - 既定から変えた: picks から計算し直す(締切前・結果待ちのレースだけ。結果が出たレースは既定のまま答え合わせする)
 */
export function raceRecommendation(race, day) {
  const p = race.prediction;
  if (!p) return null;
  const settings = getSettings();
  const oddsInput = oddsInputFor(race.race_id);
  const defaultRecs = evaluatedDefaultRecs(p, oddsInput);
  const status = raceStatus(race);
  const customSelection = !isDefaultSelection(settings) && (status === 'open' || status === 'wait');
  let bets = defaultRecs;
  let skipped = null;
  if (customSelection) {
    const r = computeRecommendation({ confidence: p.confidence, picks: p.picks }, settings, oddsInput);
    bets = r.bets;
    skipped = r.skipped;
  }
  // 期待値で絞る設定なのに、使った券種のオッズが1つも無い
  let noOdds = false;
  if (customSelection && settings.ev_filter !== 'none') {
    noOdds = CONSTANTS.bet_type_order.filter((bt) => settings.bet_types[bt]).every((bt) => (p.picks[bt] || []).every((x) => evaluatePick(x, bt, oddsInput).ev_rank === null));
  }
  return {
    settings,
    custom: customSelection,
    customAny: isCustomSettings(),
    stake: settings.stake,
    bets,
    defaultRecs,
    skipped,
    noOdds,
    oddsInput,
    manualCount: countManual(oddsInput.manual),
    hasWorker: !!oddsInput.worker,
    strategyText: strategyLabel(day.strategy),
  };
}

/** ランクの列を出すか */
export function showRankColumn(day, rec) {
  return dayHasOdds(day) || (rec && (rec.hasWorker || rec.manualCount > 0)) || getWorkerConfig().ready;
}

/** 推奨の中で最も高いランク(S1 の行の右端)。オッズが無ければ null */
export function bestRank(bets) {
  const order = ['A', 'B', 'C', 'D'];
  let best = null;
  bets.forEach((b) => {
    if (b.ev_rank && (best === null || order.indexOf(b.ev_rank) < order.indexOf(best))) best = b.ev_rank;
  });
  return best;
}

/** 今の設定の説明(S2 の説明文・S6 の要約・S3 の注記) */
export function describeSettings(settings) {
  const parts = [];
  const types = CONSTANTS.bet_type_order.filter((bt) => settings.bet_types[bt]).map((bt) => `${betTypeLabel(bt)} 上位${settings.bet_types[bt]}点`);
  parts.push(types.join(' + '));
  if (settings.min_prob !== null) parts.push(`確率${Math.round(settings.min_prob * 100)}%以上`);
  if (settings.ev_filter === 'B_or_above') parts.push('期待値B以上');
  if (settings.ev_filter === 'A_only') parts.push('期待値Aだけ');
  if (settings.skip_confidence === 'ara') parts.push('「荒」は見送り');
  if (settings.skip_confidence === 'chu_or_below') parts.push('「中」以下は見送り');
  return parts.join(' · ');
}

/** 既定の推奨との差の1行(4点以上違うときは点数だけ) */
export function diffText(rec) {
  const key = (b) => `${b.bet_type}:${b.combination}`;
  const def = new Set(rec.defaultRecs.map(key));
  const now = new Set(rec.bets.map(key));
  const removed = rec.defaultRecs.filter((b) => !now.has(key(b)));
  const added = rec.bets.filter((b) => !def.has(key(b)));
  const label = rec.strategyText ? `既定の推奨(${rec.strategyText})` : '既定の推奨';
  if (!removed.length && !added.length) return `${label}と同じ買い目です。`;
  const n = removed.length + added.length;
  if (n >= 4) return `${label}とは ${Math.max(removed.length, added.length)}点が違います。`;
  const name = (b) => (rec.bets.concat(rec.defaultRecs).some((x) => x.bet_type !== b.bet_type) ? `${betTypeLabel(b.bet_type)} ${b.combination}` : b.combination);
  const out = [`${label}とは違います。`];
  if (removed.length) out.push(`${removed.map(name).join('、')} を外し`);
  if (added.length) out.push(`${removed.length ? '、' : ''}${added.map(name).join('、')} を入れました。`);
  else out.push('ました。');
  return out.join('');
}

// ---------------------------------------------------------------- オッズの時点の帯(公開JSON)

/** odds_snapshot の帯。出さないときは '' */
export function snapshotBand(race, day, { status }) {
  if (!dayHasOdds(day)) return '';
  const snap = race.prediction && race.prediction.odds_snapshot;
  const done = status === 'result';
  if (!snap) {
    if (status !== 'open') return html`<div class="oddstime oddstime--none">${ICON.clock}<span>このレースの締切前オッズはありません。</span></div>`;
    const at = parseIso(race.closed_at) - SNAPSHOT_MINUTES_BEFORE * 60000;
    return html`<div class="oddstime oddstime--none">${ICON.clock}<span>オッズは<b>締切${SNAPSHOT_MINUTES_BEFORE}分前(${fmtClock(new Date(at).toISOString())}ごろ)</b>に取得します。取得後に期待値ランクが出ます。</span></div>`;
  }
  const t = fmtClock(snap.fetched_at);
  const before = typeof snap.minutes_before_close === 'number' ? `(締切${Math.round(snap.minutes_before_close)}分前)` : '';
  switch (snap.status) {
    case 'ok':
      return done
        ? html`<div class="oddstime oddstime--ok">${ICON.clock}<span><b>${t}時点</b>${before}のオッズでのランク</span></div>`
        : html`<div class="oddstime oddstime--ok">${ICON.clock}<span><b>${t}時点</b>${before}のオッズで期待値を計算</span></div>`;
    case 'partial':
      return html`<div class="oddstime oddstime--partial">${ICON.warn}<span><b>${t}時点</b> · 一部の券種のオッズだけ取れました</span></div>`;
    case 'late':
      return html`<div class="oddstime oddstime--late">${ICON.warn}<span><b>締切前オッズを取得できませんでした</b>(取得が間に合わず)。期待値ランクはありません。${done ? '' : '必要オッズを投票アプリのオッズと比べてください。'}</span></div>`;
    case 'failed':
      return html`<div class="oddstime oddstime--late">${ICON.warn}<span><b>締切前オッズを取得できませんでした</b>。期待値ランクはありません。</span></div>`;
    case 'not_targeted':
      return html`<div class="oddstime oddstime--not-targeted">${ICON.clock}<span>このレースはオッズ取得の対象外です</span></div>`;
    case 'stopped':
      return html`<div class="oddstime oddstime--stopped">${ICON.stop}<span>今日は締切前オッズの取得を停止しています</span></div>`;
    default:
      return '';
  }
}

// ---------------------------------------------------------------- 取得パネル(Worker)

function fmtSeconds(iso) {
  const ms = parseIso(iso);
  if (Number.isNaN(ms)) return '--:--:--';
  const d = new Date(ms + 9 * 3600000);
  const p = (n) => String(n).padStart(2, '0');
  return `${p(d.getUTCHours())}:${p(d.getUTCMinutes())}:${p(d.getUTCSeconds())}`;
}

/** エラーの表示(design/visual README v0.4 の文言案と design/screens README 13.4) */
const ERROR_VIEWS = {
  unauthorized: { tone: 'error', icon: '🔑', title: '合言葉が違います', text: '設定の合言葉を確かめてください。', action: 'settings' },
  closed: { tone: 'warn', icon: '⏱', title: '締切後のため取得しません', text: '確定オッズは翌日に反映されます。' },
  daily_limit: { tone: 'warn', icon: '⊘', title: '今日の取得上限に達しました', text: '公式サイトへの負担を抑えるため、1日の取得数に上限があります。明日(0時)に戻ります。それまでは必要オッズで判断してください。' },
  upstream: { tone: 'error', icon: '!', title: '公式サイトから取得できませんでした', text: '公式サイトが混み合っているか、一時的に応答していません。少し待ってから再試行してください。', action: 'retry' },
  stopped: { tone: 'error', icon: '■', title: '今日はオッズの取得を停止しています', text: '公式サイトから取得を断られたため、今日は自動で止めています。明日(0時)に再開します。' },
  network: { tone: 'warn', icon: '⚠', title: '通信できませんでした', text: '電波の良い場所で再試行してください。設定の URL が許可されていないアドレスの可能性もあります。', action: 'retry' },
  offline: { tone: 'warn', icon: '⚠', title: 'オフラインのため取得できません', text: 'つながったら「オッズを取る」を押してください。', action: 'retry' },
  timeout: { tone: 'error', icon: '⏱', title: '時間がかかりすぎたため打ち切りました', text: '公式サイトが混んでいる可能性があります(45秒で打ち切り)。', action: 'retry' },
  bad_request: { tone: 'error', icon: '!', title: '取得できません(要求の誤り)', text: 'アプリを再読み込みしてください。直らないときは設定の URL を確かめてください。', action: 'settings' },
  not_found: { tone: 'error', icon: '!', title: '取得できません(URL の誤り)', text: '設定の Worker の URL を確かめてください。', action: 'settings' },
  forbidden_origin: { tone: 'error', icon: '!', title: '取得できません(許可されていないアドレス)', text: 'この URL からは使えない設定です。Worker の許可するオリジンを確かめてください。', action: 'settings' },
  server_misconfigured: { tone: 'error', icon: '!', title: 'Worker の設定に問題があります', text: 'Worker の合言葉などの設定を確かめてください。', action: 'settings' },
  invalid: { tone: 'error', icon: '!', title: '応答を読み取れませんでした', text: 'Worker の版が違う可能性があります。少し待ってから再試行してください。', action: 'retry' },
};

function fetchButton({ label, sub, state = 'idle', action = 'odds-fetch', raceId, extraClass = '' }) {
  const disabled = state !== 'idle';
  return html`<button class="${cls('fetchbtn fetchbtn--compact', extraClass)}" data-state="${state}" data-action="${action}" data-race="${raceId}" ${raw(disabled ? 'disabled' : '')}>${label}<small>${sub}</small></button>`;
}

function waitLabel(ms) {
  return `あと${Math.ceil(ms / 1000)}秒`;
}

/**
 * 取得パネル。出さないときは null(呼び出し側で公開JSONの帯を出す)。
 * 締切前・今日のレースで、Worker を設定済み、または取得済みのときに出す。未設定なら設定への誘導を出す。
 */
export function fetchPanel(race, rec, day) {
  const raceId = race.race_id;
  const st = oddsState(raceId);
  const cfg = getWorkerConfig();
  const isToday = raceId.slice(0, 8) === todayJst().replace(/-/g, '');
  const open = raceStatus(race) === 'open';
  if (!isToday) return null;
  if (!st.result && !st.loading && !st.error && (!open || !cfg.ready)) {
    if (open && !cfg.ready) {
      // 公開JSONの帯があるときは、その下に控えめに出す(帯の時点の情報を消さない)
      const band = snapshotBand(race, day, { status: 'open' });
      return html`${band}<div class="${cls('fetchbox', !band && 'fetchbox--warn')}" role="note" data-fetchpanel>
        <span class="fetchbox__icon" aria-hidden="true">🔑</span>
        <div class="fetchbox__text"><span class="fetchbox__title">オッズ取得の設定がまだです</span>設定で Worker の URL と合言葉を入れると、ここで今のオッズを取れます。</div>
        <a class="fetchbtn fetchbtn--compact fetchbtn--sub" href="#/settings/worker">設定へ<small>Worker</small></a>
      </div>`;
    }
    return null;
  }
  const pages = st.loading ? st.loading.types : (st.result && st.result.types) || [];
  const estimate = Math.max(1, pages.length || 1) * SECONDS_PER_PAGE;

  // 取得中
  if (st.loading) {
    const sec = Math.floor((Date.now() - st.loading.startedAt) / 1000);
    const ratio = Math.min(0.95, sec / estimate);
    const steps = st.loading.types.map((p, i) => {
      const doneAt = (i + 1) * SECONDS_PER_PAGE;
      const k = sec >= doneAt ? 'is-done' : sec >= i * SECONDS_PER_PAGE ? 'is-now' : '';
      return html`<span class="${k}">${PAGE_LABELS[p]}</span>`;
    });
    return html`<div class="fetchbox" role="status" aria-live="polite" data-fetchpanel>
      <div class="fetchbox__text"><span class="fetchbox__title">オッズを取得中… ${sec}秒</span>公式サイトから順に取っています(${estimate}秒ほど。最大45秒で打ち切り)</div>
      <button class="fetchbtn fetchbtn--compact fetchbtn--sub" data-action="odds-cancel" data-race="${raceId}">取り消し<small>やめる</small></button>
      <div class="fetchbox__extra"><div class="progress" aria-hidden="true"><i style="width:${Math.round(ratio * 100)}%"></i></div><div class="steps">${steps}</div><span class="sr-only">目安の進み具合(${Math.round(ratio * 100)}%)</span></div>
    </div>`;
  }

  const check = canFetch(race, raceId);
  const blockView = st.block ? ERROR_VIEWS[st.block.kind] : null;
  // その日の取得の停止・上限(全レースで押せない)
  if (blockView && !st.result) {
    const quota = st.block.quota && st.block.quota.limit_per_day ? `(上限 ${st.block.quota.limit_per_day}ページ / 日)` : '';
    return html`<div class="${cls('fetchbox', `fetchbox--${blockView.tone}`)}" role="alert" data-fetchpanel>
      <span class="fetchbox__icon" aria-hidden="true">${blockView.icon}</span>
      <div class="fetchbox__text"><span class="fetchbox__title">${blockView.title}</span>${blockView.text}${quota}</div>
    </div>`;
  }

  // 失敗(直前の出どころはそのまま)
  if (st.error && st.error.kind !== 'canceled' && !(st.result && st.result.receivedAt > st.error.at)) {
    const v = ERROR_VIEWS[st.error.kind] || ERROR_VIEWS.invalid;
    let button = '';
    if (v.action === 'settings') button = html`<a class="fetchbtn fetchbtn--compact fetchbtn--sub" href="#/settings/worker">設定へ<small>${st.error.kind === 'unauthorized' ? '合言葉' : 'URL'}</small></a>`;
    else if (v.action === 'retry' && open) {
      button = check.ok
        ? fetchButton({ label: '再試行', sub: `${estimate}秒ほど`, raceId, extraClass: 'fetchbtn--sub' })
        : fetchButton({ label: '再試行', sub: check.reason === 'cooldown' ? waitLabel(check.waitMs) : '押せません', state: check.reason === 'cooldown' ? 'cooldown' : 'disabled', raceId });
    }
    return html`<div class="${cls('fetchbox', `fetchbox--${v.tone}`)}" role="alert" data-fetchpanel>
      <span class="fetchbox__icon" aria-hidden="true">${v.icon}</span>
      <div class="fetchbox__text"><span class="fetchbox__title">${v.title}</span>${v.text}</div>
      ${button}
    </div>`;
  }

  // ボタン(取得前・取得後)
  let button = '';
  if (open && !blockView) {
    if (check.ok) button = st.result ? fetchButton({ label: '更新', sub: '取り直す', raceId }) : fetchButton({ label: 'オッズを取る', sub: `${estimate}秒ほど`, raceId });
    else if (check.reason === 'cooldown') button = fetchButton({ label: '更新', sub: waitLabel(check.waitMs), state: 'cooldown', raceId });
    else if (check.reason === 'offline') button = fetchButton({ label: 'オッズを取る', sub: 'オフライン', state: 'disabled', raceId });
  }
  const manual = rec && rec.manualCount ? html` · <span class="oddssrc oddssrc--manual">${ICON.pencil}手入力</span>${rec.manualCount}点` : '';

  if (st.result) {
    const r = st.result.response;
    const failed = failedPages(st.result);
    const label = r.odds_time_label ? `(公式「${r.odds_time_label}」)` : '';
    const final = r.is_final_odds ? '締切時のオッズです。' : '';
    if (failed.length) {
      const steps = st.result.types.map((p) => html`<span class="${failed.includes(p) ? 'is-fail' : 'is-done'}">${PAGE_LABELS[p]}</span>`);
      return html`<div class="fetchbox fetchbox--warn" role="status" data-fetchpanel>
        <span class="fetchbox__icon" aria-hidden="true">!</span>
        <div class="fetchbox__text"><span class="fetchbox__title">一部の券種を取れませんでした</span><b>${fmtSeconds(r.fetched_at)} 時点</b>${manual} · 取れた券種だけで計算しています。${final}</div>
        ${button}
        <div class="fetchbox__extra"><div class="steps">${steps}</div></div>
      </div>`;
    }
    const src = r.cached
      ? html`<span class="oddssrc oddssrc--cache">${ICON.down}キャッシュ</span><b>${fmtSeconds(r.fetched_at)} 時点</b>(${r.cache_age_sec}秒前に取得したもの)`
      : html`<span class="oddssrc oddssrc--worker">${ICON.down}今のオッズ</span><b>${fmtSeconds(r.fetched_at)} 時点</b>${label}`;
    const text = rec && rec.manualCount ? '手入力した値を優先して計算しています(手入力 → Worker → 公開ランク の順)。' : '推奨・期待値をこのオッズで計算し直しました。';
    return html`<div class="fetchbox" role="status" data-fetchpanel>
      <div class="fetchbox__text">${src}${manual}<br>${final}${text}</div>
      ${button}
    </div>`;
  }

  // 取得前
  const snap = race.prediction && race.prediction.odds_snapshot;
  const pub = snap && (snap.status === 'ok' || snap.status === 'partial')
    ? html`<span class="oddssrc oddssrc--rank">公開ランク</span><b>${fmtClock(snap.fetched_at)}時点</b>のオッズで計算${manual}<br>`
    : html`<span class="oddssrc oddssrc--none">オッズなし</span>${manual}<br>`;
  return html`<div class="fetchbox" role="status" data-fetchpanel>
    <div class="fetchbox__text">${pub}ボタンで今のオッズを取り、期待値を数値で見られます。</div>
    ${button}
  </div>`;
}

/** 推奨の行の2段目(オッズと期待値の数値・手入力の欄) */
export function oddsLine(bet, { raceId, editing }) {
  const showNumbers = bet.source === 'worker' || bet.source === 'manual' || (bet.source === 'public' && isLocalData() && typeof bet.odds === 'number');
  const parts = [];
  if (editing) {
    const value = bet.source === 'manual' ? fmtOdds(bet.odds) : '';
    parts.push(html`${bet.source === 'manual' ? html`<span class="oddssrc oddssrc--manual">${ICON.pencil}手入力</span>` : html`<span class="faint">オッズ</span>`}<input class="oddsin" type="text" inputmode="decimal" value="${value}" aria-label="${bet.combination} のオッズ(手入力)" data-manual-input data-race="${raceId}" data-bt="${bet.bet_type}" data-comb="${bet.combination}">倍`);
    if (bet.source === 'manual' && typeof bet.ev === 'number') {
      parts.push(html`→ 期待値 <b class="${cls(bet.ev >= 1 && 'ev-plus')}">${bet.ev >= 1 ? '▲ ' : ''}${bet.ev.toFixed(2)}</b>`);
    }
  } else if (showNumbers) {
    if (bet.source === 'manual') parts.push(html`<span class="oddssrc oddssrc--manual">${ICON.pencil}手入力</span>`);
    if (typeof bet.odds === 'number') {
      parts.push(html`オッズ <b>${fmtOdds(bet.odds)}</b>倍 → 期待値 <b class="${cls(bet.ev >= 1 && 'ev-plus')}">${typeof bet.ev === 'number' ? `${bet.ev >= 1 ? '▲ ' : ''}${bet.ev.toFixed(2)}` : '—'}</b>`);
    } else {
      parts.push(html`オッズ <b>—</b>(この組合せは取れませんでした)`);
    }
  }
  if (!parts.length) return '';
  return html`<span class="local">${parts}</span>`;
}

/** 金額の合計行の文 */
export function stakeText(count, stake, open) {
  const total = fmtYen(count * stake);
  if (stake === 100) return open ? html`${count}点 · 1点100円で <b>${total}</b>` : html`${count}点 <b>${total}</b>`;
  return html`${count}点 · 1点${stake}円で <b>${total}</b>`;
}

