// 画面で共通に使う部品の HTML(デザイナーB の components.css のクラスを使う)
import { html, raw, cls } from './html.js';
import { helpTitle } from './wording.js';
import { parseCombination, confidenceLevel, CONFIDENCE_SHORT, CONFIDENCE_LONG, MARKS, fmtDayNumber } from './format.js';

/** 枠番バッジ。size: sm(20) / md(24) / bet(28) / lg(36)(デザイナーB の .boat--*) */
export function boat(entry, { size = 'md', absent = false, extra = '' } = {}) {
  if (!entry) return '';
  const sizeClass = size === 'md' ? '' : `boat--${size}`;
  return html`<span class="${cls('boat', sizeClass, `boat-${entry}`, absent && 'is-absent', extra)}" aria-label="${entry}号艇${absent ? '(欠場)' : ''}">${entry}</span>`;
}

/** 組合せ(枠番バッジを「-」か「=」でつなぐ)。absentEntries に入っている艇は欠場の見た目にする */
export function combo(combination, { size = 'sm', absentEntries = [] } = {}) {
  const { entries, sep } = parseCombination(combination);
  if (!entries.length) return html`<span class="faint">—</span>`;
  const parts = [];
  entries.forEach((e, i) => {
    if (i > 0) parts.push(html`<span class="combo__sep" aria-hidden="true">${sep}</span>`);
    parts.push(html`<span aria-hidden="true">${boat(e, { size, absent: absentEntries.includes(e) })}</span>`);
  });
  return html`<span class="combo" role="img" aria-label="${combination}">${parts}</span>`;
}

/** 着順(1〜3着)の組合せ。null の位置は「—」 */
export function orderCombo(order, { size = 'sm' } = {}) {
  const parts = [];
  (order || []).forEach((e, i) => {
    if (i > 0) parts.push(html`<span class="combo__sep" aria-hidden="true">-</span>`);
    parts.push(e ? html`<span aria-hidden="true">${boat(e, { size })}</span>` : html`<span class="faint" aria-hidden="true">—</span>`);
  });
  const label = (order || []).map((e) => e ?? '—').join('-');
  return html`<span class="combo" role="img" aria-label="着順 ${label}">${parts}</span>`;
}

/** 堅さ(3段の棒+文字)。long=true で S2 の言い方(堅い/普通/荒れ気味) */
export function conf(confidence, { long = false } = {}) {
  const level = confidenceLevel(confidence);
  if (!level) return '';
  const label = long ? CONFIDENCE_LONG[level] : CONFIDENCE_SHORT[level];
  return html`<span class="conf conf--${level}" title="1着確率の最大値 ${Math.round(confidence * 100)}%"><span class="conf__bars" aria-hidden="true"><i></i><i></i><i></i></span>${label}</span>`;
}

/** 印(◎○▲△) */
export function mark(pRank) {
  const m = MARKS[pRank];
  return m
    ? html`<span class="${cls('mark', pRank === 1 && 'mark--1')}" aria-label="予想${pRank}位">${m}</span>`
    : html`<span class="mark mark--none" aria-hidden="true">・</span>`;
}

/** グレードのチップ */
export function gradeChip(grade) {
  if (!grade) return '';
  return html`<span class="${cls('chip chip--grade', grade === '一般' && 'g-ippan')}">${grade}</span>`;
}

/** 'subtitle · grade 3日目' */
export function raceSubText(race, stadium) {
  return [race.subtitle, [stadium.grade, fmtDayNumber(stadium.day_number)].filter(Boolean).join(' ')].filter(Boolean).join(' · ');
}

// ---- アイコン(線画の SVG。外部ファイルを読まない) ----
const svg = (body, sw = 2) => raw(`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="${sw}" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${body}</svg>`);
export const ICONS = {
  reload: svg('<path d="M20 12a8 8 0 1 1-2.3-5.6M20 4v5h-5"/>'),
  back: svg('<path d="M15 5l-7 7 7 7"/>', 2.2),
  races: svg('<path d="M4 6h16M4 12h16M4 18h10"/>'),
  performance: svg('<path d="M3 20h18M6 16l4-5 3 3 6-7"/>'),
  analytics: svg('<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>'),
  ledger: svg('<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 10h18M7 15h4"/>'),
  more: svg('<circle cx="5" cy="12" r="1.2"/><circle cx="12" cy="12" r="1.2"/><circle cx="19" cy="12" r="1.2"/>'),
};

/** 下部タブ */
export function tabbar(current) {
  const item = (key, href, icon, label) =>
    html`<a href="${href}" ${raw(current === key ? 'aria-current="page"' : '')}>${icon}${label}</a>`;
  return html`<nav class="tabbar tabbar--5" aria-label="画面の切り替え">
    ${item('home', '#/', ICONS.races, 'レース')}
    ${item('performance', '#/performance', ICONS.performance, '成績')}
    ${item('analytics', '#/analytics/stadium', ICONS.analytics, '分析')}
    ${item('ledger', '#/ledger', ICONS.ledger, '収支')}
    ${item('about', '#/about', ICONS.more, 'その他')}
  </nav>`;
}

/** 再読み込みボタン */
export function reloadButton(busy) {
  return html`<button class="${cls('iconbtn', busy && 'is-spinning')}" data-action="reload" aria-label="再読み込み" ${raw(busy ? 'disabled' : '')}>${ICONS.reload}</button>`;
}

/** 箱(空・エラー) */
export function emptyBox({ icon = '', title, text = '', detail = '', action = null, role = '', extraClass = '' }) {
  return html`<div class="${cls('empty', extraClass)}" ${raw(role ? `role="${role}"` : '')}>
    ${icon ? html`<div class="empty__icon" aria-hidden="true">${icon}</div>` : ''}
    <div class="empty__title">${title}</div>
    ${text ? html`<div class="empty__text">${text}</div>` : ''}
    ${action || ''}
    ${detail ? html`<div class="empty__text faint" style="margin-top:16px">詳細: ${detail}</div>` : ''}
  </div>`;
}

/** 注意書き(S1・S2 の最下部) */
export function disclaimer(text = '非公式データに基づく参考情報です。正確性は保証しません。') {
  return html`<p class="disclaimer">${text}<a href="#/about">このアプリについて</a></p>`;
}

/** 用語説明の「?」(B の .help。吹き出し .helppop は押したときに main.js が出す) */
export function helpBtn(key) {
  const title = helpTitle(key);
  return html`<button class="help" type="button" data-action="help" data-help="${key}" aria-expanded="false" aria-label="${title}とは">?</button>`;
}
