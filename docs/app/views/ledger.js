// S8 収支・S9 自分の予想の成績、S2 の「自分の印・メモ」(spec/08 5.4、spec/05 11章)。
// データは端末内(IndexedDB)だけ。払戻は結果から計算する(ledger.js)。
import { html, raw, cls } from '../html.js';
import { fmtYen, fmtSignedYen, fmtRoi, betTypeLabel, fmtNumber, fmtPct } from '../format.js';
import { fmtDateTime } from '../time.js';
import { tabbar, combo, boat, reloadButton, helpBtn } from '../parts.js';
import { wilson } from '../ledger.js';
import { t } from '../wording.js';
import '../charts.js'; // デザイナーB の charts.js(window.BRCharts。依存なし)

const STATUS_LABELS = { hit: '的中', miss: '不的中', refund: '返還', special: '特払・不成立', pending: '結果待ち' };
const MARK_CHOICES = ['◎', '○', '▲', '△'];
const FEW = 200; // これ未満の件数は「参考程度」(spec/08 4章)

function appbar(title, busy) {
  return html`<header class="appbar"><h1 class="appbar__title is-pad">${title}</h1>${reloadButton(busy)}</header>`;
}

function warningBanners(warnings) {
  const key = { day: 'budget_day', month: 'budget_month' };
  return warnings.map((w) => {
    const tone = w.level === 'over' ? 'error' : 'warn';
    const text = w.kind === 'streak'
      ? t('warn.losing_streak', { n: w.streak })
      : t(`warn.${key[w.kind]}_${w.level === 'over' ? 100 : 80}`, { pct: Math.floor((w.spent / w.limit) * 100), remain: fmtNumber(Math.max(0, w.limit - w.spent)) });
    const bar = w.kind === 'streak' ? '' : html`<span class="banner__text">${fmtYen(w.spent)} / ${fmtYen(w.limit)}</span><span class="${cls('budgetbar', w.level === 'over' && 'is-over')}" style="--p:${(w.spent / w.limit).toFixed(3)}" aria-hidden="true"></span>`;
    return html`<div class="${cls('banner banner--stack', `banner--${tone}`)}" role="alert"><span class="banner__icon" aria-hidden="true">!</span><div class="banner__body"><b class="banner__title">${text}</b>${bar}</div></div>`;
  });
}

function fewNote(n) {
  return n < FEW ? html`<p class="card__note" style="padding-top:4px"><span class="lown-tag"></span> ${t('warn.few_samples', { n: fmtNumber(n) })}</p>` : '';
}

/** 累計収支の推移とドローダウン(B の BRCharts.pnl。日ごとに集計) */
function profitChart(daily) {
  if (daily.length < 2) return html`<div class="empty empty--dashed"><div class="empty__text" style="margin:0">結果が2日分以上たまると推移を出します。</div></div>`;
  return raw(window.BRCharts.pnl(daily).svg);
}

function recordForm(ui) {
  const f = ui.form || {};
  return html`<form class="set-group" data-ledger-form onsubmit="return false" style="border-top:0">
    <div class="field"><label class="field__label" for="lg-race">レース(例 20261102-02-05)</label><input class="input input--mono" id="lg-race" name="race_id" inputmode="numeric" autocomplete="off" value="${f.race_id || ''}" list="lg-races"></div>
    <div class="ledger-form__row">
      <div class="field"><label class="field__label" for="lg-bt">券種</label><select class="select" id="lg-bt" name="bet_type">${['trifecta', 'exacta', 'win'].map((b) => html`<option value="${b}" ${raw(f.bet_type === b ? 'selected' : '')}>${betTypeLabel(b)}</option>`)}</select></div>
      <div class="field"><label class="field__label" for="lg-comb">組合せ</label><input class="input input--mono" id="lg-comb" name="combination" autocomplete="off" placeholder="1-2-3" value="${f.combination || ''}"></div>
      <div class="field"><label class="field__label" for="lg-amt">金額(円)</label><input class="input input--mono" id="lg-amt" name="amount" inputmode="numeric" placeholder="100" value="${f.amount || ''}"></div>
    </div>
    ${ui.formError ? html`<p class="field__msg" style="display:flex;color:var(--c-danger)" role="alert">⚠ ${ui.formError}</p>` : ''}
    <button type="button" class="btn btn--primary" data-action="ledger-add">記録する</button>
  </form>`;
}

/** 予算のメーター(今日・今月。未入力なら案内だけ) */
function budgetCard(budget, spent, ui) {
  const meter = (name, used, limit) => (limit ? html`<div class="budgetrow"><div class="meter-row"><span>${name}</span><span><b>${fmtNumber(used)}</b> / ${fmtNumber(limit)}円</span></div><div class="${cls('meter', used >= limit ? 'is-over' : used >= limit * 0.8 && 'is-warn')}" style="--p:${Math.min(1.5, used / limit).toFixed(3)}" role="meter" aria-valuenow="${Math.round((used / limit) * 100)}" aria-valuemin="0" aria-valuemax="100" aria-label="${name}の予算"><i></i></div></div>` : '');
  const any = budget.day_limit || budget.month_limit;
  return html`<section class="card" aria-label="予算">
    <div class="card__head"><h2 class="card__title">予算</h2><span class="right"><a href="#/ledger" data-action="ledger-toggle" data-what="budget">設定 ›</a></span></div>
    <div class="card__body">${any ? html`${meter('今日', spent.day, budget.day_limit)}${meter('今月', spent.month, budget.month_limit)}` : html`<p class="card__note" style="padding:0">予算を決めると、80%と100%で知らせます(上限を超えても記録はできます)。</p>`}</div>
    ${ui.budgetOpen ? html`<form class="set-group" data-budget-form onsubmit="return false">
      <div class="ledger-form__row">
        <div class="field"><label class="field__label" for="bg-day">1日の予算(円)</label><input class="input input--mono" id="bg-day" name="day_limit" inputmode="numeric" placeholder="なし" value="${budget.day_limit || ''}"></div>
        <div class="field"><label class="field__label" for="bg-month">1か月の予算(円)</label><input class="input input--mono" id="bg-month" name="month_limit" inputmode="numeric" placeholder="なし" value="${budget.month_limit || ''}"></div>
        <div class="field"><label class="field__label" for="bg-streak">連敗の警告(回)</label><input class="input input--mono" id="bg-streak" name="streak_warn" inputmode="numeric" value="${budget.streak_warn}"></div>
      </div>
      <p class="set-help">空欄なら警告しません。入力欄から離れると保存します。</p>
    </form>` : ''}
  </section>`;
}

const RES_TEXT = { hit: '的中', miss: '不的中', refund: '返還', special: '特払・不成立', pending: '結果待ち' };
const RES_CLASS = { hit: 'res--hit', miss: 'res--miss', refund: 'res--refund', special: 'res--refund', pending: 'res--pending' };

/** S8 本体(design/screens s8-ledger.html の並び) */
export function ledgerView({ s, daily, months, warnings, budget, spent, ui, raceName, busy, dbError, backupAt, backupStale }) {
  const list = s.rows.slice().reverse().slice(0, ui.showAll ? undefined : 30);
  const range = s.rows.length ? `${fmtDateTime(s.rows[0].bet.bought_at).slice(5, 10).replace('-', '/')}〜 · ${fmtNumber(s.count)}件` : '';
  return html`
    <header class="appbar"><h1 class="appbar__title is-pad">収支</h1>${reloadButton(busy)}<button class="iconbtn" data-action="ledger-toggle" data-what="form" aria-label="記録を足す" aria-expanded="${String(!!ui.formOpen)}">＋</button></header>
    ${dbError ? html`<div class="banner banner--error" role="alert"><span class="banner__icon">!</span><div class="banner__body"><b class="banner__title">端末内のデータを使えません</b><span class="banner__text">${dbError}</span></div></div>` : ''}
    ${backupStale ? html`<div class="banner banner--warn" role="note"><span class="banner__icon">!</span><div class="banner__body"><span class="banner__title">${t('warn.backup')}</span></div></div>` : ''}
    ${warningBanners(warnings)}
    ${ui.formOpen ? html`<section class="card" aria-label="記録を足す"><div class="card__head"><h2 class="card__title">記録を足す</h2></div>${recordForm(ui)}</section>` : ''}
    ${budgetCard(budget, spent, ui)}
    <section class="card" aria-label="収支">
      <div class="card__head"><h2 class="card__title">収支</h2><span class="right">${range}</span></div>
      <div class="kpi">
        <div><div class="k">購入(結果の出た分)</div><div class="v">${fmtYen(s.stake)}</div></div>
        <div><div class="k">払戻</div><div class="v">${fmtYen(s.ret)}</div></div>
        <div><div class="k">収支</div><div class="${cls('v', s.profit >= 0 ? 'is-plus' : 'is-minus')}">${fmtSignedYen(s.profit)}</div></div>
        <div class="${cls(s.races < FEW && 'is-lown')}"><div class="k">回収率 ${helpBtn('help.roi')}</div><div class="v">${s.roi === null ? '—' : fmtRoi(s.roi)}</div>${s.races < FEW ? html`<div class="k">${t('warn.few_samples', { n: fmtNumber(s.races) })}</div>` : ''}</div>
        <div><div class="k">最大ドローダウン ${helpBtn('help.drawdown')}</div><div class="v">${fmtYen(s.maxDrawdown)}</div></div>
        <div><div class="k">連敗(今 / 最大) ${helpBtn('help.losing_streak')}</div><div class="v">${s.currentStreak} / ${s.maxStreak}</div></div>
        ${s.pendingStake ? html`<div class="wide"><div class="k">結果待ちの購入</div><div class="v">${fmtYen(s.pendingStake)}</div></div>` : ''}
      </div>
      <div class="card__body">${profitChart(daily)}</div>
    </section>
    <div class="sechead">記録 <span class="count">新しい順</span><span class="right">的中・不的中は同じ大きさ</span></div>
    <div class="list" aria-label="購入記録">
      ${list.length ? list.map((row) => html`<div class="ledger-row">
        <div class="ledger-row__main"><span class="faint small">${fmtDateTime(row.bet.bought_at).slice(5).replace('-', '/')}</span><br><a href="#/race/${row.bet.race_id}"><b>${raceName(row.bet.race_id)} ${betTypeLabel(row.bet.bet_type)}</b></a><br>${combo(row.bet.combination, { size: 'sm' })} <span class="small faint">${row.bet.source === 'model' ? t('label.model_top') : row.bet.source === 'mixed' ? 'モデル+自分' : '自分'}</span></div>
        <div class="ledger-row__num"><span class="${cls('res', RES_CLASS[row.status])}">${RES_TEXT[row.status]}${row.payout !== null ? ` ${fmtSignedYen(row.payout - row.bet.amount)}` : ''}</span><span class="small faint">${fmtNumber(row.bet.amount)}円 → ${row.payout !== null ? `${fmtNumber(row.payout)}円` : '—'}</span></div>
        <button class="iconbtn" data-action="ledger-delete" data-id="${row.bet.id}" aria-label="この記録を消す">✕</button>
      </div>`) : html`<p class="card__note" style="padding-top:8px">まだ記録がありません。レース詳細の「${t('label.record_bet')}」か、右上の「＋」から記録できます。</p>`}
      ${s.rows.length > 30 ? html`<button class="more more--inset" data-action="ledger-all">${ui.showAll ? 'たたむ' : `すべて表示(${s.rows.length}件)`}</button>` : ''}
    </div>
    <p class="disclaimer" style="margin-top:6px">払戻は結果から自動で計算します(保存しません)。</p>
    ${months.length ? html`<section class="card" aria-label="月別"><div class="card__head"><h2 class="card__title">月別</h2></div><div class="card__body" style="padding:8px 0 4px"><div class="tbl-wrap"><table class="tbl"><thead><tr><th>月</th><th>件数</th><th>購入</th><th>払戻</th><th>収支</th></tr></thead><tbody>${months.map((m) => html`<tr><td>${m.month}</td><td>${m.count}</td><td>${fmtNumber(m.stake)}</td><td>${fmtNumber(m.ret)}</td><td class="${m.ret - m.stake >= 0 ? 'is-plus' : 'is-minus'}">${fmtSignedYen(m.ret - m.stake)}</td></tr>`)}</tbody></table></div></div></section>` : ''}
    <section class="card" aria-label="バックアップ">
      <div class="card__head"><h2 class="card__title">バックアップ</h2><span class="right">${backupAt ? `最終 ${fmtDateTime(backupAt).slice(5, 10).replace('-', '/')}` : '未実施'}</span></div>
      <p class="card__note" style="padding-top:4px">${t('notice.local_records')}</p>
      <div class="backup-btns"><button class="btn" data-action="backup-export">${t('label.backup_export')}</button><label class="btn">${t('label.backup_import')}<input type="file" accept="application/json,.json" data-backup-import hidden></label></div>
      ${ui.backupMsg ? html`<p class="card__note" role="status">${ui.backupMsg}</p>` : ''}
    </section>
    <p class="disclaimer">${t('warn.losing_streak', { n: budget.streak_warn || 5 }).replace(/^.*?。/, '')} <a href="#/analytics/you">あなたの収支分析</a> · <a href="#/marks">自分の予想の成績</a> · <a href="#/about">困ったときの相談先</a></p>
    ${tabbar('ledger')}`;
}

/** S9 自分の予想の成績 */
export function marksView({ st, total, loadedDays, busy }) {
  const ci = (k, n) => { const w = wilson(k, n); return w ? `95%区間 ${fmtPct(w[0], 0)}〜${fmtPct(w[1], 0)}` : ''; };
  const rate = (k, n) => (n ? fmtPct(k / n) : '—');
  const few = st.races < FEW;
  return html`
    <header class="appbar"><a class="iconbtn" href="#/performance" aria-label="成績へ戻る">‹</a><h1 class="appbar__title">自分の予想の成績</h1>${reloadButton(busy)}</header>
    <section class="card">
      <div class="card__head"><h2 class="card__title">印の成績</h2><span class="right">結果のある ${fmtNumber(st.races)}R(印を付けたのは ${fmtNumber(total)}R)</span></div>
      <div class="${cls('kpi', few && 'is-lown')}">
        <div><div class="k">◎の1着率</div><div class="v">${rate(st.honWin, st.races)}</div><div class="k">${ci(st.honWin, st.races)}</div></div>
        <div><div class="k">モデルの本命の1着率(同じレース)</div><div class="v">${rate(st.modelWin, st.races)}</div><div class="k">${ci(st.modelWin, st.races)}</div></div>
        <div><div class="k">◎の連対率</div><div class="v">${rate(st.honTop2, st.races)}</div><div class="k">${ci(st.honTop2, st.races)}</div></div>
        <div><div class="k">◎-○-▲ の3連単(1点100円と仮定)</div><div class="v">${st.triRoi === null ? '—' : fmtRoi(st.triRoi)}</div><div class="k">${fmtNumber(st.triRaces)}R · 投資 ${fmtYen(st.triStake)} → 払戻 ${fmtYen(st.triRet)}</div></div>
      </div>
      ${fewNote(st.races)}
    </section>
    <p class="disclaimer">${t('notice.simulation')} ${t('notice.local_records')} 集計は読み込んだ日(${fmtNumber(loadedDays)}日分)のレースだけです。</p>
    ${tabbar('performance')}`;
}

/** S2: 自分の印・メモ */
export function marksCard({ race, marks, note }) {
  const boats = race.prediction ? race.prediction.boats.map((b) => b.entry) : [1, 2, 3, 4, 5, 6];
  return html`<section class="card" aria-label="自分の印・メモ">
    <div class="card__head"><h2 class="card__title">${t('label.my_marks')}・メモ</h2><span class="minetag">端末内</span><span class="right">この端末にだけ保存</span></div>
    <div class="card__body">
      ${boats.map((e) => html`<div class="markrow">${boat(e)}<div class="markpick markpick--mine" role="radiogroup" aria-label="${e}号艇の印">${MARK_CHOICES.map((m) => html`<button role="radio" aria-checked="${String(marks[e] === m)}" data-action="set-mark" data-race="${race.race_id}" data-entry="${e}" data-mark="${m}" aria-label="${e}号艇に${m}">${m}</button>`)}</div></div>`)}
      <label class="field__label" for="memo-${race.race_id}" style="margin-top:8px">メモ(500文字まで)</label>
      <textarea class="input memo" id="memo-${race.race_id}" maxlength="500" rows="3" data-memo data-race="${race.race_id}">${note}</textarea>
    </div>
  </section>`;
}

/** あなたの収支分析(W-21)。切り口ごとに件数・回収率と区間・的中率。後で「分析」タブの「あなた」に移す */
export function analysisView({ sections, total, busy, subtabs }) {
  const cell = (g) => {
    if (g.level === 'insufficient') return html`<td colspan="2" class="faint">${t('ana.insufficient', { n: g.races })}</td>`;
    const straddle = g.ci && g.ci[0] < 1 && g.ci[1] > 1;
    return html`<td><span class="${cls('ci-inline', straddle && 'is-straddle')}">${fmtRoi(g.roi)}${g.ci ? html`<small>(${Math.round(g.ci[0] * 100)}〜${Math.round(g.ci[1] * 100)})</small>` : ''}</span></td>
      <td>${fmtPct(g.hitRate, 0)}${g.hitCi ? html`<small class="faint">(${Math.round(g.hitCi[0] * 100)}〜${Math.round(g.hitCi[1] * 100)})</small>` : ''}</td>`;
  };
  return html`
    <header class="appbar"><h1 class="appbar__title is-pad">${t('ana.tab')}</h1>${reloadButton(busy)}</header>
    ${subtabs}
    <p class="disclaimer" style="margin-top:12px">🔒 ${t('ana.own_local')} 結果の出た ${fmtNumber(total)}レース。回収率の区間はレース単位のブートストラップ(200回)、的中率の区間は Wilson です。</p>
    ${sections.map(({ title, rows }) => html`<section class="card" aria-label="${title}">
      <div class="card__head"><h2 class="card__title">${title}</h2><span class="right">回収率(95%区間)${helpBtn('help.confidence_interval')}</span></div>
      ${rows.length ? html`<div class="tbl-wrap"><table class="tbl"><thead><tr><th class="l">区分</th><th>件数</th><th>回収率</th><th>的中率</th></tr></thead>
      <tbody>${rows.map((g) => html`<tr class="${cls(g.level === 'few' && 'is-lown')}"><td class="l">${g.key}${g.level === 'few' ? html` <span class="lown-tag" title="${t('ana.few', { n: g.races })}"></span>` : ''}</td><td>${fmtNumber(g.races)}R<br><small class="faint">${fmtNumber(g.bets)}点</small></td>${cell(g)}</tr>`)}</tbody></table></div>` : html`<p class="card__note" style="padding-top:8px">まだ結果の出た記録がありません。</p>`}
    </section>`)}
    <p class="disclaimer">${t('notice.simulation')} この数字は振り返り用です。賭け金を増やす方向の提案はしません。</p>
    ${tabbar('analytics')}`;
}
