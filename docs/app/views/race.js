// S2 レース詳細。design/screens README 5章
import { html, raw, cls } from '../html.js';
import { fmtClock, fmtUpdated, fmtMonthDay, minutesUntil } from '../time.js';
import {
  pctInt, fmtComboPct, fmtYen, fmtSignedYen, fmtOdds, fmtEv, betTypeLabel, fmtNumber, fmtPct,
} from '../format.js';
import { boat, combo, conf, mark, raceSubText, disclaimer, emptyBox, ICONS } from '../parts.js';
import { evaluatePicks } from '../strategy.js';
import { t } from '../wording.js';
import { marksCard } from './ledger.js';
import { tenkaiCard } from './phase3b.js';
import { helpBtn } from '../parts.js';
import { syntheticOdds, allocateBets, allocationSummary, ALLOCATION_METHODS, NEEDS_ODDS } from '../betsum.js';

import {
  raceRecommendation, showRankColumn, evRankBadge, fetchPanel, snapshotBand, oddsLine, stakeText, describeSettings, diffText,
  RANK_LEGEND, ODDS_NOTE,
} from '../odds-ui.js';
import {
  raceStatus, recommendedOf, outcomeKind, betOutcome, payoutFor, anyHitProb, includesAbsent,
} from '../model.js';

const TAB_ORDER = ['win', 'exacta', 'trifecta', 'quinella', 'trio', 'place', 'quinella_place'];

/** 一覧(S1)へのリンク */
export function homeHref(date, isToday) {
  return isToday ? '#/' : `#/date/${date}`;
}

/** 残り時間(S2 のヘッダ・アプリバー用) */
function countdown(race, { inAppbar = false } = {}) {
  const min = minutesUntil(race.closed_at);
  const urgent = min <= 5;
  let body;
  if (min <= 1) body = html`まもなく`;
  else if (min >= 60) body = html`<small>あと</small> ${Math.floor(min / 60)}<small>時間</small>${min % 60}<small>分</small>`;
  else body = html`<small>あと</small> ${min}<small>分</small>`;
  return html`<span class="${cls('racehead__cd', urgent && 'is-urgent', inAppbar && 'appbar__cd')}" ${raw(inAppbar ? 'aria-hidden="true"' : '')}>${body}</span>`;
}

function appbar({ title, backHref, updatedText, race, status }) {
  return html`<header class="appbar">
    <a class="iconbtn" href="${backHref}" aria-label="一覧へ戻る">${ICONS.back}</a>
    <h1 class="appbar__title">${title}</h1>
    ${updatedText ? html`<span class="appbar__updated">${updatedText}</span>` : ''}
    ${race && status === 'open' ? countdown(race, { inAppbar: true }) : ''}
  </header>`;
}

/** ヘッダ(締切・残り時間・予想の種類・堅さ) */
function raceHead({ race, stadium, status, dayStatus }) {
  let right;
  if (status === 'open') right = countdown(race);
  else if (status === 'canceled') right = html`<span class="chip chip--cancel">中止</span>`;
  else if (status === 'wait') right = html`<span class="chip chip--wait">締切済み · 結果待ち</span>`;
  else right = html`<span class="chip chip--pre">${dayStatus === 'final' ? '確定' : '結果確定'}</span>`;
  const p = race.prediction;
  let kind = '';
  if (p && status !== 'canceled') {
    kind = p.variant === 'preview'
      ? html`<span class="chip chip--preview">● 直前予想 ${fmtClock(p.predicted_at)}</span>`
      : html`<span class="chip chip--pre">前日予想 ${fmtClock(p.predicted_at)}</span>`;
  }
  return html`<div class="racehead" data-racehead>
    <div class="racehead__row"><span class="racehead__close">締切 <b>${fmtClock(race.closed_at)}</b></span>${right}</div>
    <div class="racehead__sub"><span>${raceSubText(race, stadium)}</span>${kind}${p && status === 'open' ? conf(p.confidence, { long: true }) : ''}</div>
  </div>`;
}

/** 必要オッズの箱「≧ 32.3 倍」 */
function reqBox(requiredOdds) {
  return html`<span class="req"><span class="req__ge">≧</span><span class="req__val">${fmtOdds(requiredOdds)}</span><span class="req__unit">倍</span></span>`;
}

/** 推奨買い目のカード(期待値ランク・オッズの出どころ・設定での計算し直しを含む) */
function recommendedCard({ race, day, status, rec, ui }) {
  const isOpen = status === 'open';
  const hasEv = showRankColumn(day, rec) || (isOpen && !!ui.manualMode[race.race_id]);
  const absent = race.result ? race.result.absent_entries || [] : [];
  const panel = fetchPanel(race, rec, day) || snapshotBand(race, day, { status });
  const manualMode = isOpen && !!ui.manualMode[race.race_id];
  const head = rec.custom
    ? html`<div class="card__head"><h2 class="card__title">${t('label.model_top')}の買い目</h2><span class="custom-tag">あなたの設定</span><span class="right"><a href="#/settings">設定 ›</a></span></div>`
    : html`<div class="card__head"><h2 class="card__title">${t('label.model_top')}の買い目</h2><span class="right">${rec.bets.length ? rec.strategyText : '0点'}</span></div>`;
  const hint = rec.custom
    ? html`<p class="picks-hint">${describeSettings(rec.settings)}</p>`
    : (isOpen ? html`<p class="picks-hint">投票アプリのオッズが <b>必要オッズ以上</b> なら買い(期待値プラス)</p>` : '');
  const notes = [];
  const anyNumbers = rec.hasWorker || rec.manualCount > 0;
  if (hasEv && status !== 'result') notes.push(html`<p class="oddsnote">${ODDS_NOTE}</p>`);
  if (anyNumbers) notes.push(html`<p class="oddsnote">取得・手入力したオッズはこの端末にだけ表示します(取得したオッズは保存・送信しません)。</p>`);

  if (!rec.bets.length) {
    const description = (day.strategy && (day.strategy.description || day.strategy.short_label)) || '';
    let box;
    if (rec.custom && rec.skipped === 'confidence') {
      box = html`<div class="skip-box"><b>見送り</b><span>設定(${rec.settings.skip_confidence === 'ara' ? '「荒」なら見送り' : '「中」以下なら見送り'})により見送りました。</span></div>`;
    } else if (rec.custom && rec.noOdds && rec.settings.no_odds === 'skip') {
      box = html`<div class="skip-box"><b>見送り</b><span>期待値で絞る設定ですが、オッズが無いため見送りました。<br>(設定: オッズが無いときは「見送る」)</span></div>`;
    } else if (rec.custom) {
      box = html`<div class="skip-box"><b>見送り</b><span>今の設定の条件に合う組合せがありません。</span></div>`;
    } else {
      const reason = race.prediction.skip_reason;
      box = html`<div class="skip-box"><b>見送り ${helpBtn('help.skip')}</b><span>${t(reason ? `skip.${reason}` : 'skip.none')}${description ? raw('<br>') : ''}${description ? `条件: ${description}` : ''}</span></div>`;
    }
    const defaultNote = rec.custom && rec.defaultRecs.length
      ? html`<p class="card__note">既定の推奨は ${rec.defaultRecs.length}点(${rec.defaultRecs[0].combination} ほか)。確率上位の必要オッズも参考に。</p>`
      : (status !== 'result' ? html`<p class="card__note">買う場合は下の「確率上位」の必要オッズを参考に。</p>` : '');
    return html`<section class="card" aria-label="推奨買い目">
      ${head}${hint}${panel}${box}${defaultNote}${notes}
    </section>`;
  }

  const unselected = new Set(ui.betUnselected[race.race_id] || []);
  const rows = rec.bets.map((bet) => {
    const out = betOutcome(race, bet);
    let cap = '';
    let rowClass = '';
    if (out && out.kind === 'hit') {
      rowClass = 'is-hit';
      cap = html`<span class="cap cap--hit">✓ 的中 ${fmtOdds(out.amount / 100)}倍</span>`;
    } else if (out && out.kind === 'refund') {
      rowClass = 'is-refund';
      cap = html`<span class="cap cap--refund">返還</span>`;
    } else if (out && out.kind === 'special') {
      rowClass = 'is-refund';
      cap = html`<span class="cap cap--refund">${out.label} ${fmtYen(out.amount)}</span>`;
    }
    const line = hasEv || manualMode ? oddsLine(bet, { raceId: race.race_id, editing: manualMode }) : '';
    const key = `${bet.bet_type}:${bet.combination}`;
    const picked = !unselected.has(key);
    const pick = isOpen ? html`<span class="checkbox" role="checkbox" tabindex="0" aria-checked="${String(picked)}" aria-label="${bet.combination} を合計に入れる" data-action="betsel" data-race="${race.race_id}" data-key="${key}"></span>` : '';
    return html`<div class="${cls('pick-row', hasEv && 'has-ev', rowClass)}">
      <span class="pickcell">${pick}${combo(bet.combination, { size: 'bet', absentEntries: absent })}</span>
      <span class="prob">${fmtComboPct(bet.prob)}</span>
      <span class="reqodds">${reqBox(bet.required_odds)}${cap}</span>
      ${hasEv ? html`<span class="evcol">${evRankBadge(bet.ev_rank)}</span>` : ''}
      ${line}
    </div>`;
  });
  const goodCount = rec.bets.filter((b) => b.ev_rank === 'A' || b.ev_rank === 'B').length;
  const stake = rec.custom || rec.customAny ? rec.stake : 100;
  const total = html`<div class="pick-total"><span>${stakeText(rec.bets.length, stake, isOpen)}</span><span>どれか的中 <b>${fmtComboPct(anyHitProb(rec.bets))}</b>${hasEv ? html` · B以上 <b>${goodCount}点</b>` : ''}</span></div>`;
  const extra = [];
  if (hasEv) extra.push(html`<div class="evlegend">${RANK_LEGEND}</div>`);
  if (rec.custom) extra.push(html`<p class="oddsnote">${diffText(rec)}</p>`);
  if (rec.custom && rec.noOdds && rec.settings.no_odds === 'prob_only') extra.push(html`<p class="oddsnote">オッズが無いため、確率だけで選びました。</p>`);
  // S7: 投票アプリで見たオッズを手入力する(手入力が最も優先される)
  if (isOpen) {
    extra.push(betsumPanel({ race, rec, unselected, alloc: ui.alloc }));
    extra.push(html`<div class="manualbar"><button class="manualbtn" data-action="manual-mode" data-race="${race.race_id}" aria-pressed="${String(manualMode)}">${manualMode ? '✓ 手入力を終える' : '✎ オッズを手入力して期待値を見る'}</button>${rec.manualCount ? html`<button class="manualbtn" data-action="manual-clear" data-race="${race.race_id}">手入力を消す(${rec.manualCount}点)</button>` : ''}</div>`);
  }
  return html`<section class="${cls('card', isOpen && 'card--emph')}" aria-label="推奨買い目">
    ${head}${hint}${panel}
    <div class="${cls('pick-head', hasEv && 'has-ev')}"><span>組合せ</span><span>確率</span><span>必要オッズ</span>${hasEv ? html`<span>ランク</span>` : ''}</div>
    ${rows}
    ${total}
    ${extra}
    ${notes}
  </section>`;
}

/** 各艇の確率のカード */
function boatsCard({ race }) {
  const p = race.prediction;
  const result = race.result;
  const absent = result ? result.absent_entries || [] : [];
  const order = result ? result.order || [] : [];
  const isPreview = p.variant === 'preview' && p.boats.some((b) => b.exhibition_time !== null && b.exhibition_time !== undefined);
  const times = p.boats.filter((b) => !absent.includes(b.entry) && typeof b.exhibition_time === 'number').map((b) => b.exhibition_time);
  const fastest = times.length ? Math.min(...times) : null;
  const rows = p.boats.map((b) => {
    const isAbsent = absent.includes(b.entry);
    const placeIndex = order.indexOf(b.entry);
    const extra = [];
    if (isPreview && typeof b.exhibition_time === 'number') {
      const best = b.exhibition_time === fastest;
      extra.push(best
        ? html`<span>展示 <span class="best-time">${b.exhibition_time.toFixed(2)}</span></span>`
        : html`<span>展示 ${b.exhibition_time.toFixed(2)}</span>`);
    }
    if (isPreview && b.exhibition_course) {
      const changed = b.exhibition_course !== b.entry;
      extra.push(html`<span class="${cls(changed && 'hl')}">進入 ${b.exhibition_course}${changed ? html`<span class="sr-only">(枠番と違う)</span>` : ''}</span>`);
    }
    return html`<div class="${cls('racer', b.p_rank === 1 && 'is-top', isAbsent && 'is-absent')}">
      <span class="racer__waku">${boat(b.entry, { size: 'lg', absent: isAbsent })}</span>
      <div class="racer__name">${mark(b.p_rank)}<span class="racer__nm">${b.racer_number && b.racer_name ? html`<button class="racer-link" type="button" data-action="card-open" data-racer="${b.racer_number}" data-entry="${b.entry}" data-name="${b.racer_name}" aria-haspopup="dialog">${b.racer_name}</button>` : (b.racer_name || (b.racer_number ? `登録 ${b.racer_number}` : '—'))}</span>${isAbsent
        ? html`<span class="chip chip--cancel">欠場</span>`
        : html`<span class="racer__rank">${[b.rank, b.branch].filter(Boolean).join(' · ')}</span>${b.flying_count > 0 ? html`<span class="flag-f">F${b.flying_count}</span>` : ''}${placeIndex >= 0 ? html`<span class="racer__place">${placeIndex + 1}着</span>` : ''}`}</div>
      <div class="racer__pwin">${pctInt(b.p_win)}<small>%</small></div>
      <div class="racer__bar"><div class="${cls('pbar', b.p_rank === 1 && 'is-top')}" style="--p:${Math.max(0, Math.min(1, b.p_win || 0))}" role="img" aria-label="1着確率 ${pctInt(b.p_win)}%"><span class="pbar__fill"></span></div><span class="racer__sub">${isAbsent ? '予想時の値' : html`2着内 <b>${pctInt(b.p_top2)}</b> 3着内 <b>${pctInt(b.p_top3)}</b>`}</span></div>
      ${extra.length ? html`<div class="racer__extra">${extra}</div>` : ''}
      ${Array.isArray(b.reasons) && b.reasons.length && !isAbsent ? html`<ul class="reasons racer__reasons" aria-label="${b.entry}号艇の確率の根拠">${b.reasons.slice(0, 3).map((r) => html`<li class="reason reason--${r.direction === 'down' ? 'down' : 'up'}" data-strength="${r.strength}"><span class="reason__dir">${t(r.direction === 'down' ? 'label.reason_down' : 'label.reason_up')}</span><span class="reason__label">${r.label}</span><span class="reason__val num">${fmtReasonValue(r)}</span><span class="reason__str" aria-label="強さ ${r.strength}/3"><i></i><i></i><i></i></span></li>`)}</ul>` : ''}
    </div>`;
  });
  const right = isPreview ? '展示・進入つき' : result ? '1着確率 · 着順' : '1着確率';
  return html`<section class="card" aria-label="各艇の確率">
    <div class="card__head"><h2 class="card__title">各艇の確率 ${helpBtn('help.probability')}</h2><span class="right">${right}${p.boats.some((x) => x.reasons && x.reasons.length) ? html` · 根拠 ${helpBtn('help.reasons')}` : ''}</span></div>
    <div class="card__body card__body--flush">${rows}</div>
    <div class="legend"><span>◎○▲△ = 1着確率の1〜4位</span><span>2着内・3着内は %</span>${isPreview ? html`<span>★ = 展示タイム最速</span><span>黄地 = 枠番と違う進入</span>` : ''}</div>
  </section>`;
}

/** 確率上位(券種タブ)。推奨タグは今の推奨(設定で計算し直したもの)に合わせる */
function rankCard({ race, day, tab, more, rec, rankTypes }) {
  const picks = race.prediction.picks || {};
  let tabs = TAB_ORDER.filter((t) => Array.isArray(picks[t]) && picks[t].length);
  const shown = tabs.filter((t) => !rankTypes || rankTypes.includes(t));
  if (shown.length) tabs = shown;
  if (!tabs.length) return '';
  const current = tabs.includes(tab) ? tab : tabs[tabs.length - 1];
  const hasEv = showRankColumn(day, rec);
  const evaluated = evaluatePicks(picks, current, rec.oddsInput);
  const limit = more ? evaluated.length : 10;
  const recs = rec.bets.filter((b) => b.bet_type === current).map((b) => b.combination);
  const absent = race.result ? race.result.absent_entries || [] : [];
  const rows = evaluated.slice(0, limit).map((item, i) => {
    const isRec = recs.includes(item.combination);
    const isHit = !!payoutFor(race.result, current, item.combination);
    return html`<div class="${cls('rank-row', hasEv && 'has-ev', isRec && 'is-rec', isHit && 'is-hit')}">
      <span class="no">${i + 1}</span>
      <span>${combo(item.combination, { size: 'sm', absentEntries: absent })}${isHit ? html`<span class="tag tag--hit">✓的中</span>` : isRec ? html`<span class="tag">推奨</span>` : ''}</span>
      <span class="p">${fmtComboPct(item.prob)}</span>
      <span class="o">${fmtOdds(item.required_odds)}<small>倍</small></span>
      ${hasEv ? evRankBadge(item.ev_rank, { sm: true }) : ''}
    </div>`;
  });
  const moreButton = evaluated.length > 10
    ? html`<button class="more more--inset" data-action="rank-more" data-race="${race.race_id}" aria-expanded="${String(!!more)}">${more ? html`11位以下をたたむ <span aria-hidden="true">▲</span>` : html`11〜${evaluated.length}位を表示 <span aria-hidden="true">▼</span>`}</button>`
    : '';
  return html`<section class="card" aria-label="確率上位">
    <div class="card__head"><h2 class="card__title">確率上位</h2><span class="right">${hasEv ? '期待値つき · ' : ''}右は必要オッズ</span></div>
    <div class="tabs" role="tablist" aria-label="券種">${tabs.map((t) => html`<button role="tab" data-action="rank-tab" data-tab="${t}" aria-selected="${String(t === current)}">${betTypeLabel(t)}</button>`)}</div>
    <div role="tabpanel">${rows}</div>
    ${moreButton}
  </section>`;
}

/** 選手データ(たたむ) */
function playersCard({ race, open }) {
  const boats = race.prediction.boats;
  const absent = race.result ? race.result.absent_entries || [] : [];
  const valid = boats.filter((b) => !absent.includes(b.entry));
  const maxOf = (key) => Math.max(...valid.map((b) => (typeof b[key] === 'number' && b[key] > 0 ? b[key] : -Infinity)));
  const minOf = (key) => Math.min(...valid.map((b) => (typeof b[key] === 'number' && b[key] > 0 ? b[key] : Infinity)));
  const best = { national_win_rate: maxOf('national_win_rate'), local_win_rate: maxOf('local_win_rate'), motor_top2: maxOf('motor_top2'), avg_st: minOf('avg_st') };
  const cell = (b, key, digits) => {
    const v = b[key];
    if (v === null || v === undefined || (key === 'local_win_rate' && v === 0)) return html`<td>—</td>`;
    const isBest = !absent.includes(b.entry) && v === best[key];
    return html`<td>${isBest ? html`<span class="best-value">${v.toFixed(digits)}</span>` : v.toFixed(digits)}</td>`;
  };
  return html`<section class="card">
    <details class="fold" data-fold="players" ${raw(open ? 'open' : '')}>
      <summary>選手データ <span class="count">勝率・モーター・ST・F</span></summary>
      <div class="tbl-wrap" data-noswipe>
        <table class="tbl">
          <thead><tr><th class="l">枠</th><th class="l">支部</th><th>全国</th><th>当地</th><th>モ2連</th><th>平均ST</th><th>F</th></tr></thead>
          <tbody>${boats.map((b) => html`<tr class="${cls(absent.includes(b.entry) && 'is-absent')}">
            <td>${boat(b.entry, { size: 'sm' })}</td><td class="l">${b.branch || '—'}</td>
            ${cell(b, 'national_win_rate', 2)}${cell(b, 'local_win_rate', 2)}${cell(b, 'motor_top2', 1)}${cell(b, 'avg_st', 2)}
            <td>${b.flying_count > 0 ? html`<span class="flag-f">F${b.flying_count}</span>` : '0'}</td></tr>`)}</tbody>
        </table>
      </div>
      <p class="legend" style="padding-top:8px">太字 = レース内の最高(STは最小)。当地「—」= 当地のデータなし。</p>
    </details>
  </section>`;
}

/** 確定オッズでの期待値(final_odds が null なら出さない。[] なら「該当なし」)。公開版はランクだけ、ローカル版は数値も */
function finalOddsCard({ race }) {
  const fo = race.result && race.result.final_odds;
  if (fo === null || fo === undefined) return '';
  const recs = recommendedOf(race);
  const reqOf = (b) => {
    const r = recs.find((x) => x.bet_type === b.bet_type && x.combination === b.combination);
    return r ? r.required_odds : null;
  };
  const head = html`<div class="card__head"><h2 class="card__title">確定オッズでの期待値</h2><span class="right">参考(締切後のオッズ)</span></div>`;
  if (!fo.length) {
    return html`<section class="card">${head}<p class="card__note" style="padding-top:8px">推奨買い目が無いため、確定オッズでの期待値はありません。</p></section>`;
  }
  const numeric = fo.some((b) => typeof b.odds === 'number');
  return html`<section class="card">
    ${head}
    <div class="ev-row ev-row--head"><span>推奨買い目</span><span>${numeric ? '確定オッズ' : '必要オッズ'}</span><span class="evv" style="font-weight:400">${numeric ? '期待値' : 'ランク'}</span></div>
    ${fo.map((b) => numeric
      ? html`<div class="ev-row">${combo(b.combination, { size: 'sm' })}<span>${fmtOdds(b.odds)}倍 <span class="faint">/ 必要 ${fmtOdds(reqOf(b))}</span></span><span class="${cls('evv', b.ev >= 1 ? 'is-plus' : 'is-minus')}">${fmtEv(b.ev)} ${evRankBadge(b.ev_rank, { sm: true })}</span></div>`
      : html`<div class="ev-row">${combo(b.combination, { size: 'sm' })}<span class="faint">≧ ${fmtOdds(reqOf(b))}倍</span><span class="evv">${evRankBadge(b.ev_rank)}</span></div>`)}
    <div class="evlegend">${RANK_LEGEND}</div>
    <p class="card__note" style="padding-top:8px">${numeric ? '▲ = 期待値1.0以上(確定オッズが必要オッズを上回った)。' : 'ランクは確定オッズで計算した期待値の区分です(数値は公開しません)。'}締切前にこのオッズで買えたとは限らない。</p>
  </section>`;
}

/** 結果のカード */
function resultCard({ race }) {
  const result = race.result;
  const kind = outcomeKind(race);
  const order = [0, 1, 2].map((i) => (result.order || [])[i] ?? null);
  const absent = result.absent_entries || [];
  const o = result.recommended_outcome;
  let outcome = '';
  if (race.prediction && o) {
    const recs = recommendedOf(race);
    const hitBets = recs.filter((b) => payoutFor(result, b.bet_type, b.combination));
    const refundCount = o.refunded ?? recs.filter((b) => includesAbsent(b.combination, absent)).length;
    const detail = html`<span class="detail">投資 ${fmtYen(o.stake)}(${o.bets}点) → 払戻 ${fmtYen(o.return)}${kind === 'refund' ? '(返還分)' : ''} · 収支 <b class="${o.return - o.stake >= 0 ? 'is-plus' : 'is-minus'}">${fmtSignedYen(o.return - o.stake)}</b></span>`;
    if (kind === 'skip') {
      outcome = html`<div class="rblock__outcome"><span class="chip chip--skip">見送り</span><span>買い目なし</span></div>`;
    } else if (kind === 'hit') {
      outcome = html`<div class="rblock__outcome is-hit"><span class="res res--hit">的中</span><span>${hitBets.map((b) => `${betTypeLabel(b.bet_type)} ${b.combination}`).join('、')}</span><span class="amount">${fmtYen(o.return)}</span>${detail}</div>`;
    } else if (kind === 'refund') {
      outcome = html`<div class="rblock__outcome"><span class="res res--refund">返還</span><span>${refundCount ? `返還 ${refundCount}点` : '返還あり'}</span><span class="amount">${fmtYen(o.return)}</span>${detail}</div>`;
    } else {
      const bt = recs[0] ? recs[0].bet_type : 'trifecta';
      const win = (result.payouts && result.payouts[bt] || []).find((p) => p.combination);
      outcome = html`<div class="rblock__outcome"><span class="res res--miss">不的中</span><span>${win ? `${betTypeLabel(bt)} ${win.combination} は推奨外` : ''}</span><span class="amount">${fmtYen(o.return)}</span>${detail}</div>`;
    }
  }
  const payoutRows = [];
  ['win', 'exacta', 'trifecta'].forEach((bt) => {
    (result.payouts && result.payouts[bt] || []).forEach((p) => {
      payoutRows.push(html`<span class="bt">${betTypeLabel(bt)}</span><span>${p.combination ? combo(p.combination, { size: 'sm' }) : html`<span class="chip chip--refund">${p.label || '—'}</span>`}</span><span class="amt">${fmtYen(p.amount)}</span>`);
    });
  });
  return html`<section class="${cls('card', kind === 'hit' && 'card--hit')}" aria-label="結果">
    <div class="card__head"><h2 class="card__title">結果</h2>${absent.length ? html`<span class="right">欠場 ${absent.join('・')}号艇</span>` : ''}</div>
    <div class="rblock">
      <div class="rblock__order">${order.map((e, i) => html`<span class="rblock__pos">${e ? boat(e, { size: 'lg' }) : html`<span class="rblock__nopos" aria-label="未確定">—</span>`}${i + 1}着</span>`)}</div>
      ${result.technique ? html`<span class="rblock__tech">${result.technique}</span>` : ''}
    </div>
    ${outcome}
    ${payoutRows.length ? html`<div class="payouts" aria-label="払戻(100円あたり)">${payoutRows}</div>` : ''}
  </section>`;
}

/** 予想のブロック(推奨・各艇・確率上位・選手データ) */
function predictionBlocks(ctx) {
  const { race, day, status, prefs, ui } = ctx;
  const rec = raceRecommendation(race, day);
  return [
    recommendedCard({ race, day, status, rec, ui }),
    tenkaiCard(race.prediction),
    boatsCard({ race }),
    ui.local ? marksCard({ race, marks: ui.local.marks[race.race_id] || {}, note: ui.local.notes[race.race_id] || '' }) : '',
    rankCard({ race, day, tab: prefs.rankTab || (day.strategy && day.strategy.bet_type), more: ui.rankMore, rec, rankTypes: prefs.rankTypes }),
    playersCard({ race, open: prefs.playersOpen }),
  ];
}

/** たたむブロック(中身はカードのまま) */
function foldBlock(key, raceId, title, count, open, inner) {
  return html`<section class="card"><details class="fold" data-fold="${key}" data-race="${raceId}" ${raw(open ? 'open' : '')}>
    <summary>${title} <span class="count">${count}</span></summary>
    <div class="fold__inner">${inner}</div>
  </details></section>`;
}

/** 前後Rバー */
function raceNav({ stadium, index, date, isToday }) {
  const prev = stadium.races[index - 1];
  const next = stadium.races[index + 1];
  const note = (r) => {
    const s = raceStatus(r);
    if (s === 'result') return '結果';
    if (s === 'canceled') return '中止';
    return fmtClock(r.closed_at);
  };
  const num = String(stadium.stadium_number).padStart(2, '0');
  return html`<nav class="racenav" aria-label="前後のレース">
    ${prev ? html`<a class="prev" href="#/race/${prev.race_id}" data-replace aria-label="前のレース ${prev.race_number}R">‹ ${prev.race_number}R <small>${note(prev)}</small></a>` : html`<span class="prev disabled" aria-hidden="true">‹</span>`}
    <a class="racenav__list" href="${homeHref(date, isToday)}" data-action="goto-stadium" data-stadium="${num}">${stadium.stadium_name}</a>
    ${next ? html`<a class="next" href="#/race/${next.race_id}" data-replace aria-label="次のレース ${next.race_number}R"><small>${note(next)}</small> ${next.race_number}R ›</a>` : html`<span class="next disabled" aria-hidden="true">›</span>`}
  </nav>`;
}

/** S2 本体 */
export function raceView(ctx) {
  const { day, found, date, isToday, banners, prefs, ui } = ctx;
  const { stadium, race, index } = found;
  const status = raceStatus(race);
  const title = `${isToday ? '' : `${fmtMonthDay(date)} `}${stadium.stadium_name} ${race.race_number}R`;
  const result = race.result;
  const absent = result ? result.absent_entries || [] : [];
  const p = race.prediction;
  const blocks = [];
  const full = { race, day, status, prefs, ui };

  if (absent.length) {
    blocks.push(html`<div class="banner banner--warn" role="note"><span class="banner__icon" aria-hidden="true">!</span><div class="banner__body"><b class="banner__title">${absent.join('・')}号艇が欠場</b><span class="banner__text">${absent.join('・')}を含む買い目は返還(払戻に計上)。確率は欠場前の予想のまま表示しています。</span></div></div>`);
  }
  if (status === 'canceled') {
    blocks.push(emptyBox({ icon: '⊘', title: 'このレースは中止になりました', text: '舟券は全額返還です。成績(回収率)の計算からは外しています。' }));
    if (p) {
      const fav = p.boats.find((b) => b.p_rank === 1);
      const count = `推奨${recommendedOf(race).length}点${fav ? ` · ◎${fav.entry}` : ''}`;
      blocks.push(foldBlock('canceled-pred', race.race_id, '中止前の予想を見る', count, ui.folds['canceled-pred'], predictionBlocks(full)));
    }
  } else if (!p) {
    blocks.push(emptyBox({ icon: '—', title: 'このレースの予想はありません', text: '出走表のデータが欠けていたため、予想できませんでした。' }));
    if (result) blocks.push(resultCard({ race }));
  } else if (status === 'result') {
    blocks.push(resultCard({ race }));
    if (result.final_odds !== null && result.final_odds !== undefined) {
      // 翌日(確定オッズ読込済み): 結果 → 期待値 → 予想の内容はたたむ
      blocks.push(finalOddsCard({ race }));
      blocks.push(foldBlock('final-pred', race.race_id, '推奨買い目・各艇の確率', '予想の内容', ui.folds['final-pred'], predictionBlocks(full)));
    } else {
      blocks.push(...predictionBlocks(full));
    }
  } else {
    if (status === 'wait') {
      blocks.push(html`<div class="banner banner--muted" role="note"><span class="banner__icon" aria-hidden="true">ℹ</span><div class="banner__body">結果は次のデータ更新で反映されます。</div></div>`);
    }
    blocks.push(...predictionBlocks(full));
  }

  return html`
    ${appbar({ title, backHref: homeHref(date, isToday), updatedText: `更新 ${fmtUpdated(day.generated_at, date)}`, race, status })}
    ${banners}
    ${raceHead({ race, stadium, status, dayStatus: day.status })}
    ${versionBar(race)}
    ${previewCard(race)}
    ${blocks}
    ${disclaimer('非公式データに基づく参考情報です。舟券の購入は自己責任で。')}
    ${raceNav({ stadium, index, date, isToday })}`;
}

/** 読み込み中(URL からわかる場名・R番号を先に出す) */
export function raceSkeleton({ title, backHref }) {
  return html`
    ${appbar({ title, backHref })}
    <div class="racehead"><span class="sk" style="width:60%;height:22px"></span><span class="sk" style="width:80%;height:14px;margin-top:8px"></span></div>
    <section class="card" aria-busy="true"><div class="card__body"><span class="sk" style="width:40%;height:16px"></span><span class="sk" style="height:44px;margin-top:12px"></span><span class="sk" style="height:44px;margin-top:8px"></span><span class="sk" style="height:44px;margin-top:8px"></span></div></section>
    <section class="card"><div class="card__body"><span class="sk" style="width:40%;height:16px"></span><span class="sk" style="height:40px;margin-top:12px"></span><span class="sk" style="height:40px;margin-top:8px"></span></div></section>
    <span class="sr-only" role="status">読み込み中</span>`;
}

/** レースが見つからない・読めない */
export function raceErrorView({ title, backHref, banners, box }) {
  return html`${appbar({ title, backHref })}${banners}${box}`;
}

/** 根拠の値: 0〜1 の割合はラベルが「率」なら % にする(単位はラベルで判断) */
function fmtReasonValue(r) {
  if (r.value === null || r.value === undefined) return '';
  if (typeof r.value === 'number' && r.value > 0 && r.value <= 1 && /率/.test(r.label) && !/2連率|勝率/.test(r.label)) return `${Math.round(r.value * 100)}%`;
  return typeof r.value === 'number' ? String(r.value) : String(r.value);
}

/** 予想の版と確定時刻(spec/08 5.1) */
function versionBar(race) {
  const p = race.prediction;
  if (!p) return '';
  const version = p.version || p.variant;
  const lock = p.locked_at || p.predicted_at;
  return html`<div class="versionbar"><span class="chip ${version === 'preview' ? 'chip--preview' : 'chip--pre'}">${t(version === 'preview' ? 'label.version_preview' : 'label.version_pre')}</span>
    <span>🔒 ${t('label.locked_at', { time: fmtClock(lock) })}</span>${p.model_id ? html`<span class="faint">· ${p.model_id}</span>` : ''} ${helpBtn('help.prediction_version')}</div>`;
}

/** 直前情報(preview_info があるとき)。前日版の予想には使っていないことを書く */
function previewCard(race) {
  const p = race.prediction;
  // preview_info はレース直下に置かれる(古い形でも読めるよう prediction 内も見る)
  const info = race.preview_info || (p && p.preview_info);
  if (!info || typeof info !== 'object') return '';
  const boats = Array.isArray(info.boats) ? info.boats : [];
  const w = [info.weather, (info.wind_direction || info.wind_class) && `風 ${info.wind_direction || ({ head: '向かい風', tail: '追い風', cross: '横風', calm: '無風' }[info.wind_class] || info.wind_class)}`, info.wind_speed !== undefined && info.wind_speed !== null && `${info.wind_speed}m`, info.wave_height !== undefined && info.wave_height !== null && `波 ${info.wave_height}cm`, info.temperature !== undefined && info.temperature !== null && `気温 ${info.temperature}℃`].filter(Boolean).join(' · ');
  return html`<section class="card" aria-label="直前情報">
    <div class="card__head"><h2 class="card__title">直前情報</h2><span class="right">${p && (p.version || p.variant) === 'preview' ? '直前版の予想に使用' : '予想には未使用'}</span></div>
    ${boats.length ? html`<div class="tbl-wrap" data-noswipe><table class="tbl"><thead><tr><th class="l">枠</th><th>展示</th><th>進入</th><th>展示ST</th></tr></thead><tbody>${boats.map((b) => html`<tr><td>${boat(b.entry, { size: 'sm' })}</td><td>${b.exhibition_time ?? '—'}</td><td>${b.exhibition_course ?? '—'}</td><td>${b.exhibition_st ?? b.start_exhibition ?? '—'}</td></tr>`)}</tbody></table></div>` : ''}
    ${w ? html`<p class="card__note" style="padding-top:8px">${w}</p>` : ''}
  </section>`;
}

/** 買い目合計(spec/08 5.5・5.6)。チェックした行について、1レースの予算と配分のしかたから買い目ごとの金額を出す。
 *  配分はこのレースの予算・確率・オッズだけで決める(過去の収支・連敗は使わない) */
const ALLOC_LABELS = { equal: '均等', top: '上位厚め', prob: '確率に比例', payout: '払戻をそろえる', ev: '期待値に比例' };
function betsumPanel({ race, rec, unselected, alloc }) {
  const sel = rec.bets.filter((b) => !unselected.has(`${b.bet_type}:${b.combination}`));
  const odds = sel.map((b) => (typeof b.odds === 'number' ? b.odds : null));
  const hasOdds = sel.length > 0 && odds.every((o) => o !== null);
  const budget = alloc.budget === null ? sel.length * 100 : alloc.budget;
  const method = NEEDS_ODDS.includes(alloc.method) && !hasOdds ? 'equal' : alloc.method;
  const inputs = sel.map((b) => ({ prob: b.prob, odds: typeof b.odds === 'number' ? b.odds : null }));
  const r = allocateBets(method, inputs, budget);
  const amounts = r.ok || r.reason === 'no_positive_ev' ? r.amounts : sel.map(() => 0);
  const sum = allocationSummary(inputs, amounts);
  const syn = hasOdds ? syntheticOdds(odds) : null;
  const notes = [];
  if (budget <= 0) notes.push(t('alloc.no_budget'));
  if (!hasOdds && NEEDS_ODDS.includes(alloc.method)) notes.push(t('alloc.need_odds'));
  if (r.reason === 'torigami') notes.push(t('warn.torigami_unavoidable'));
  if (r.reason === 'no_positive_ev') notes.push(t('alloc.no_positive_ev'));
  if (r.capped) notes.push(t('alloc.cap_applied'));
  if (r.ok && r.short && sel.length * 100 > budget) notes.push(t('alloc.budget_too_small'));
  const seg = html`<div class="seg seg--alloc" role="radiogroup" aria-label="配分のしかた">${ALLOCATION_METHODS.map((m) => {
    const disabled = NEEDS_ODDS.includes(m) && !hasOdds;
    return html`<button role="radio" aria-checked="${String(m === method)}" data-action="alloc-method" data-value="${m}" ${raw(disabled ? `disabled title="${t('alloc.need_odds')}"` : '')}>${ALLOC_LABELS[m]}</button>`;
  })}</div>`;
  const rows = sel.map((b, i) => html`<tr class="${cls(amounts[i] === 0 && 'is-zero')}"><td>${betTypeLabel(b.bet_type)} ${b.combination}</td><td>${fmtComboPct(b.prob)}</td><td><b>${fmtYen(amounts[i])}</b></td>${hasOdds ? html`<td>${fmtYen(Math.floor(amounts[i] * odds[i]))}</td>` : ''}</tr>`);
  return html`<div class="betsum racebetsum" aria-label="買い目合計">
    <div class="betsum__budget">
      <span class="betsum__k">1レースの予算</span>
      <span class="stepper" role="group" aria-label="1レースの予算">
        <button data-action="alloc-budget" data-delta="-100" aria-label="100円減らす" ${raw(budget <= 100 ? 'disabled' : '')}>−</button>
        <output aria-live="polite">${fmtNumber(budget)}<small>円</small></output>
        <button data-action="alloc-budget" data-delta="100" aria-label="100円増やす" ${raw(budget >= 100000 ? 'disabled' : '')}>＋</button>
      </span>
      ${alloc.budget !== null ? html`<button class="btn btn--small btn--text" data-action="alloc-budget-reset">点数×100円に戻す</button>` : ''}
    </div>
    <div class="betsum__method"><span class="betsum__k">配分のしかた ${helpBtn('help.allocation')}${method === 'ev' ? helpBtn('help.allocation_ev') : ''}${method === 'payout' ? helpBtn('help.allocation_payout') : ''}</span>${seg}</div>
    <p class="card__note alloc-note">${t('alloc.not_history')}</p>
    ${notes.length ? html`<p class="card__note alloc-warn" role="status">${notes.join(' ')}</p>` : ''}
    <table class="betsum__alloc"><thead><tr><th>買い目</th><th>確率</th><th>金額</th>${hasOdds ? html`<th>当たったときの払戻</th>` : ''}</tr></thead><tbody>${rows}</tbody></table>
    <div class="betsum__grid">
      <div class="betsum__cell"><span class="betsum__k">合計</span><span class="betsum__v num">${fmtNumber(r.total || 0)}<small>円</small></span></div>
      <div class="betsum__cell"><span class="betsum__k">どれかが当たる確率</span><span class="betsum__v num">${fmtPct(sum.anyHit, 1)}</span></div>
      <div class="betsum__cell"><span class="betsum__k">合成オッズ ${helpBtn('help.combined_odds')}</span>${syn !== null ? html`<span class="betsum__v num">${syn.toFixed(1)}<small>倍</small></span>` : html`<span class="betsum__v is-none">オッズなし</span>`}</div>
    </div>
    ${hasOdds && r.total > 0 ? html`<p class="card__note" style="padding-top:8px">当たったときの払戻 ${fmtYen(sum.payoutMin)}〜${fmtYen(sum.payoutMax)} · 期待される払戻 ${fmtYen(Math.round(sum.expected))}(合計の ${fmtPct(sum.expected / r.total, 0)})</p>` : ''}
    <div class="betsum__foot">
      ${hasOdds && method === 'payout' && r.ok ? html`<span class="trigami trigami--ok">どれが当たっても払戻が合計以上 ${helpBtn('help.torigami')}</span>` : ''}
      <button class="btn" data-action="copy-bets" data-race="${race.race_id}" ${raw(r.total > 0 ? '' : 'disabled')}>${t('label.copy_bets')}</button>
      <button class="btn" data-action="record-recs" data-race="${race.race_id}" ${raw(r.total > 0 ? '' : 'disabled')}>${t('label.record_bet')}</button>
    </div>
    <p class="card__note" style="padding-top:8px">${t('notice.self_responsibility')} <a href="https://www.boatrace.jp/" target="_blank" rel="noopener noreferrer">公式サイト(トップ)</a> — ${t('notice.official_link')}</p>
  </div>`;
}

/** 表示中の配分(コピー・購入を記録で使う)。betsumPanel と同じ計算 */
export function currentAllocation(rec, unselected, alloc) {
  const sel = rec.bets.filter((b) => !unselected.has(`${b.bet_type}:${b.combination}`));
  const hasOdds = sel.length > 0 && sel.every((b) => typeof b.odds === 'number');
  const budget = alloc.budget === null ? sel.length * 100 : alloc.budget;
  const method = NEEDS_ODDS.includes(alloc.method) && !hasOdds ? 'equal' : alloc.method;
  const r = allocateBets(method, sel.map((b) => ({ prob: b.prob, odds: typeof b.odds === 'number' ? b.odds : null })), budget);
  return sel.map((b, i) => ({ bet: b, amount: r.ok ? r.amounts[i] : 0 })).filter((x) => x.amount > 0);
}
