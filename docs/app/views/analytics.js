// S10 分析(spec/11、DA-7、文言 spec/09 8章): 場 / 今日の選手 / モデル / 買い方(W-19・W-20)。
// 件数の扱いは BRCharts.RULES(200 / 30、src/common/analytics_rules.py と同じ)。30未満は率を出さない。
import { html, raw, cls } from '../html.js';
import { fmtNumber, fmtPct, fmtRoi, betTypeLabel } from '../format.js';
import { helpBtn } from '../parts.js';
import { t } from '../wording.js';
import '../charts.js';

const C = () => window.BRCharts;
const lvl = (x) => x.level || (x.n !== undefined ? (C().nClass(x.n) === 'none' ? 'insufficient' : C().nClass(x.n) === 'few' ? 'few' : 'normal') : 'normal');
const insuf = (n) => html`<span class="insuff">${t('ana.insufficient', { n: fmtNumber(n) })}</span>`;
const refTag = (n) => html` <span class="lown-tag" title="${t('ana.few', { n })}"></span>`;
const rateCell = (v, lo, hi, x, n) => (lvl(x) === 'insufficient' || v === null || v === undefined
  ? html`<td colspan="1">${insuf(n)}</td>`
  : html`<td class="rate">${fmtPct(v, 1)}${lo !== undefined && lo !== null ? html`<small>(${Math.round(lo * 100)}〜${Math.round(hi * 100)})</small>` : ''}</td>`);
const chips = (action, list, current, { group = false } = {}) => html`<div class="fchips" role="${group ? 'group' : 'radiogroup'}">${list.map(([v, name, disabled]) => html`<button class="fchip" ${raw(group ? `aria-pressed="${current.includes(v)}"` : `role="radio" aria-checked="${String(v === current)}"`)} data-action="${action}" data-value="${v}" ${raw(disabled ? 'disabled aria-describedby="cube-reason"' : '')}>${name}</button>`)}</div>`;
const filter = (label, body) => html`<div class="filter"><span class="filter__label">${label}</span>${body}</div>`;
const card = (title, right, body) => html`<section class="card"><div class="card__head"><h2 class="card__title">${title}</h2>${right ? html`<span class="right">${right}</span>` : ''}</div><div class="card__body">${body}</div></section>`;
const loading = html`<div class="emptybox" role="status">読み込み中…</div>`;

const TECH = [['share_nige', 'tk-nige', '逃げ'], ['share_sashi', 'tk-sashi', '差し'], ['share_makuri', 'tk-makuri', 'まくり'], ['share_makurisashi', 'tk-makurizashi', 'まくり差し'], ['share_nuki', 'tk-other', '抜き'], ['share_megumare', 'tk-other', '恵まれ']];
const COND_LABELS = { all: 'すべて', day: '昼', night: 'ナイター', spring: '春', summer: '夏', autumn: '秋', winter: '冬', none: '無風', head: '向かい風', tail: '追い風', cross: '横風', general: '一般', g2g3: 'G2・G3' };
const STRENGTH = { weak: '弱', mid: '中', strong: '強' };
const condName = (c) => {
  if (COND_LABELS[c]) return COND_LABELS[c];
  const m = /^(head|tail|cross)_(weak|mid|strong)$/.exec(String(c));
  return m ? `${COND_LABELS[m[1]]}・${STRENGTH[m[2]]}` : String(c).replace(/_/g, ' ');
};

// ---------------------------------------------------------------- 場
export function stadiumTab({ data, cube, ui }) {
  if (!data) return loading;
  const ids = Object.keys(data.stadiums).map(Number).sort((a, b) => a - b);
  const sid = ids.includes(ui.stadium) ? ui.stadium : ids[0];
  const period = ui.period === '365d' ? '365d' : 'all';
  const st = data.stadiums[sid][period] || {};
  const nat = data.stadiums[0] && data.stadiums[0][period];
  const p = data.periods[period] || {};
  const sum = st.summary || {};
  const course = (st.course || []).map((c) => ({ label: `${c.course}コース`, value: c.win_rate, n: c.n, lo: c.win_lo, hi: c.win_hi }));
  const ref = nat && nat.course && nat.course[0] ? nat.course[0].win_rate : null;
  const conds = st.conditions || {};
  const condRows = Object.entries(conds).flatMap(([cut, rows]) => rows.map((r) => ({ cut, ...r })));
  const stackRows = condRows.filter((r) => r.share_nige !== null && r.share_nige !== undefined).map((r) => ({ label: r.cut === 'all' ? 'すべて' : condName(r.cond), n: r.n_races, parts: TECH.map(([k, cls2, name]) => ({ key: cls2, name, p: r[k] || 0 })) }));
  const condName2 = (r) => (r.cut === 'all' ? 'すべて' : `${r.cut === 'wind' ? '風' : r.cut === 'season' ? '季節' : r.cut}: ${condName(r.cond)}`);
  const condBody = html`<table class="atable"><thead><tr><th>条件</th><th>件数</th><th>1コース1着率</th></tr></thead><tbody>${condRows.map((r) => html`<tr class="${cls(r.level === 'few' && 'is-lown')}"><td>${condName2(r)}${r.level === 'few' ? refTag(r.n_races) : ''}</td><td class="n">${fmtNumber(r.n_races)}</td>${rateCell(r.c1_win_rate, r.c1_win_lo, r.c1_win_hi, r, r.n_races)}</tr>`)}</tbody></table><p class="card__note" style="padding:6px 0 0">${t('ana.result_weather')}</p>`;
  const condTable = condRows.length ? card('条件ごとの1コース1着率', '', condBody) : '';
  return html`
    <div class="stabs" role="tablist" aria-label="場">${ids.map((i) => html`<button role="tab" data-action="ana-stadium" data-value="${i}" aria-selected="${String(i === sid)}">${data.stadiums[i].name}</button>`)}</div>
    ${filter('期間', chips('ana-period', [['all', '全期間'], ['365d', '直近365日']], period))}
    <p class="disclaimer" style="margin-top:4px">${t('ana.period', { from: p.from || '', to: p.to || '', n: fmtNumber(sum.n_races || 0) })}</p>
    ${card('コース別の1着率', ref !== null ? `点線 = 全国の1コース ${fmtPct(ref, 0)}` : '', raw(C().hbars(course, { ref, refLabel: '全国1コース' })))}
    ${card('荒れやすさ', '', sum.level === 'insufficient' ? insuf(sum.n_races) : html`<p>万舟率 <b>${fmtPct(sum.manshu_rate, 1)}</b>(${Math.round((sum.manshu_lo || 0) * 100)}〜${Math.round((sum.manshu_hi || 0) * 100)}%)${sum.level === 'few' ? refTag(sum.n_races) : ''} · 3連単の払戻 中央値 ${fmtNumber(sum.trifecta_median)}円</p><p class="card__note" style="padding:4px 0 0">${t('ana.manshu')}</p>`)}
    ${stackRows.length ? card('決まり手の割合', '条件ごと', raw(C().stacked(stackRows))) : ''}
    ${condTable}
    ${cubeCard({ cube, ui, name: data.stadiums[sid].name })}`;
}

// 条件で集計(キューブ)。選べる組合せは事前集計の切り口だけ
const CUTS = { '': 'course', wind: 'course_wind', rank: 'course_rank', grade: 'course_grade', season: 'course_season', 'rank,wind': 'course_wind_rank' };
const cutOf = (dims) => CUTS[[...dims].sort().join(',')] || null;
function cubeCard({ cube, ui, name }) {
  if (!cube) return card(`条件で集計(${name})`, '', loading);
  const sel = ui.cube || {};
  const course = sel.course || '1';
  const dims = ['wind', 'rank', 'grade', 'season'].filter((d) => sel[d] && sel[d] !== 'all');
  const cut = cutOf(dims);
  const values = (d) => [...new Set(cube.rows.map((r) => r[d]))].filter((v) => v !== 'all');
  const dimChips = (d, label) => {
    const opts = [['all', 'すべて']].concat(values(d).map((v) => [v, condName(v), !cutOf(new Set([...dims.filter((x) => x !== d), d]))]));
    return filter(label, chips('ana-cube', opts.map(([v, n, dis]) => [`${d}:${v}`, n, dis]), `${d}:${sel[d] || 'all'}`));
  };
  const row = cut && cube.rows.find((r) => r.cut === cut && r.course === course && ['wind', 'rank', 'grade', 'season'].every((d) => r[d] === (sel[d] || 'all')));
  const result = !cut ? html`<p class="card__note">${t('ana.not_precomputed')}</p>`
    : !row ? html`<p class="card__note">この条件の集計はありません。</p>`
      : row.level === 'insufficient' ? insuf(row.n_races)
        : html`<table class="atable"><tbody>
          <tr class="${cls(row.level === 'few' && 'is-lown')}"><td>${course}コースの1着率${row.level === 'few' ? refTag(row.n_races) : ''}</td><td class="n">${fmtNumber(row.n_starts)}走</td><td class="rate">${fmtPct(row.win_rate, 1)}<small>(${Math.round(row.win_lo * 100)}〜${Math.round(row.win_hi * 100)})</small></td></tr>
          <tr><td>2着以内 / 3着以内</td><td></td><td class="rate">${fmtPct(row.top2_rate, 0)} / ${fmtPct(row.top3_rate, 0)}</td></tr>
        </tbody></table>`;
  return card(`条件で集計(${name})`, 'コースは必ず選ぶ', html`
    ${filter('コース', chips('ana-cube', ['1', '2', '3', '4', '5', '6'].map((c) => [`course:${c}`, `${c}コース`]), `course:${course}`))}
    ${dimChips('wind', '風')}${dimChips('rank', '級別')}${dimChips('grade', 'グレード')}${dimChips('season', '季節')}
    <p class="fchip-reason" id="cube-reason">押せない条件は、${t('ana.not_precomputed')}</p>
    ${result}`);
}

// ---------------------------------------------------------------- 今日の選手
const RANK_KINDS = { nige: '逃げ率', avg_st: '平均ST', sashi: '差し率', makuri: 'まくり率', motor: 'モーター', series: '今節' };
export function playersTab({ data, ui, stadiumName }) {
  if (data === undefined) return loading;
  if (!data) return html`<div class="emptybox">今日のランキングはまだありません。</div>`;
  const kinds = Object.keys(data.rankings);
  const kind = kinds.includes(ui.rkind) ? ui.rkind : kinds[0];
  const scopes = Object.keys(data.rankings[kind] || {}).map(Number).sort((a, b) => a - b);
  const scope = scopes.includes(ui.rscope) ? ui.rscope : scopes[0];
  const list = ((data.rankings[kind] || {})[scope] || []).filter((x) => (x.n ?? 0) >= C().RULES.minN).slice(0, 10);
  const isPct = kind !== 'avg_st';
  return html`
    ${filter('種類', chips('ana-rkind', kinds.map((k) => [k, RANK_KINDS[k] || k]), kind))}
    ${filter('場', chips('ana-rscope', scopes.map((s) => [String(s), s === 0 ? '全場' : stadiumName(s)]), String(scope)))}
    ${card(`${RANK_KINDS[kind] || kind}の上位`, `${data.date} 出走の選手`, list.length ? html`<table class="atable"><thead><tr><th>選手</th><th>出走</th><th>件数</th><th>値</th></tr></thead><tbody>${list.map((x) => html`<tr class="${cls(x.n < C().RULES.okN && 'is-lown')}"><td>${x.rank}. ${x.racer_name}${x.n < C().RULES.okN ? refTag(x.n) : ''}</td><td class="n">${stadiumName(x.stadium)} ${x.race}R ${x.entry}号艇${x.motor ? ` · M${x.motor}` : ''}</td><td class="n">${fmtNumber(x.n)}</td><td class="rate">${isPct ? fmtPct(x.value, 1) : x.value.toFixed(2)}</td></tr>`)}</tbody></table>` : html`<p class="card__note">件数30以上の選手がいません。</p>`)}`;
}

// 場番号 → 場名(モデルの弱点の「場」の切り口で使う)
const STADIUM_NAMES = ['桐生', '戸田', '江戸川', '平和島', '多摩川', '浜名湖', '蒲郡', '常滑', '津', '三国', 'びわこ', '住之江', '尼崎', '鳴門', '丸亀', '児島', '宮島', '徳山', '下関', '若松', '芦屋', '福岡', '唐津', '大村'];
const stadiumName = (k) => STADIUM_NAMES[Number(k) - 1] || String(k);

// ---------------------------------------------------------------- モデル
export function modelTab({ weak, weekly, weeks, ui }) {
  if (!weak) return loading;
  const v = ui.mver === 'preview' ? 'preview' : 'pre';
  const conds = (weak.conditions && weak.conditions[v]) || {};
  const groups = Object.entries(conds).filter(([k]) => k !== 'all');
  const all = (conds.all || [])[0];
  const wk = weekly;
  return html`
    <div class="hindsight" role="note" style="margin:8px var(--gutter) 0"><div><b>${t('ana.live_only')}</b>${weak.period ? `${weak.period.from}〜${weak.period.to}` : ''} · 前日版 ${fmtNumber(weak.races.pre)}R / 直前版 ${fmtNumber(weak.races.preview)}R</div></div>
    ${filter('予想の版', chips('ana-mver', [['pre', t('label.version_pre')], ['preview', t('label.version_preview')]], v))}
    ${all ? card('全体', '', html`<p>予測の平均 ${fmtPct(all.pred_mean, 1)} / 実際 ${fmtPct(all.actual, 1)}(${fmtNumber(all.n)}件)${all.level === 'few' ? refTag(all.n) : ''}</p>`) : ''}
    ${card('外れやすい条件', `差 = 予測 − 実際 ${''}`, html`${groups.map(([g, rows]) => html`<h3 class="atable-h">${{ stadium: '場', prob_band: '確率の帯', technique: '決まり手', weather: '天候', wind: '風' }[g] || g}</h3>
      <table class="atable"><thead><tr><th>条件</th><th>件数</th><th>予測</th><th>実際(95%区間)</th><th>差</th></tr></thead><tbody>${rows.slice().sort((a, b) => b.n - a.n).map((r) => html`<tr class="${cls(r.level === 'few' && 'is-lown')}"><td>${g === 'stadium' ? stadiumName(r.key) : condName(r.key)}${r.level === 'few' ? refTag(r.n) : ''}</td><td class="n">${fmtNumber(r.n)}</td>${r.level === 'insufficient' ? html`<td colspan="3">${insuf(r.n)}</td>` : html`<td>${fmtPct(r.pred_mean, 0)}</td><td class="rate">${fmtPct(r.actual, 0)}${r.ci95 ? html`<small>(${Math.round(r.ci95[0] * 100)}〜${Math.round(r.ci95[1] * 100)})</small>` : ''}</td><td>${r.diff >= 0 ? '+' : '−'}${Math.abs(Math.round(r.diff * 1000) / 10)}pt</td>`}</tr>`)}</tbody></table>`)}`)}
    ${card('週ごとのまとめ', '', html`${filter('週', chips('ana-week', weeks.map((w) => [w, w]), ui.week || weeks[weeks.length - 1]))}
      ${!wk ? loading : html`<p>${wk.from}〜${wk.to} · ${fmtNumber(wk.races[v])}R</p>
        ${wk.calibration && wk.calibration[v] ? html`<p>ECE ${wk.calibration[v].level === 'insufficient' ? insuf(wk.calibration[v].races) : wk.calibration[v].ece}${wk.calibration[v].level === 'few' ? refTag(wk.calibration[v].races) : ''} ${helpBtn('help.calibration')}</p>` : ''}
        ${(wk.strategies && wk.strategies[v] || []).length ? html`<table class="atable"><thead><tr><th>買い方</th><th>件数</th><th>回収率</th></tr></thead><tbody>${wk.strategies[v].map((s) => html`<tr class="${cls(s.races < C().RULES.okN && 'is-lown')}"><td>${s.short_label}${s.is_baseline ? html` <span class="chip chip--pre">基準</span>` : ''}</td><td class="n">${fmtNumber(s.races)}</td>${s.races < C().RULES.minN || s.roi === null ? html`<td>${insuf(s.races)}</td>` : html`<td class="rate">${fmtRoi(s.roi)}${s.roi_ci95 ? html`<small>(${Math.round(s.roi_ci95[0] * 100)}〜${Math.round(s.roi_ci95[1] * 100)})</small>` : ''}</td>`}</tr>`)}</tbody></table>` : ''}`}`)}
    <p class="disclaimer"><a href="#/performance">成績(較正・買い方の比較)を見る</a></p>`;
}

// ---------------------------------------------------------------- 買い方(シミュレーター)
export function simTab({ data, ui, personas }) {
  const people = personasBlock({ data: personas, ui });
  if (!data) return html`${people}${loading}`;
  const src = ui.simsrc === 'live' ? 'live' : 'validation';
  const v = ui.mver === 'preview' ? 'preview' : 'pre';
  const block = src === 'validation' ? data.validation : (data.live || {})[v] || { rows: [] };
  let rows = (block.rows || []).slice();
  if (ui.simsort === 'roi') rows.sort((a, b) => (b.roi ?? -1) - (a.roi ?? -1));
  const p = block.period || {};
  return html`
    ${people}
    <h2 class="sechead">買い方のシミュレーター</h2>
    ${filter('期間', chips('ana-simsrc', [['validation', t('label.backtest')], ['live', t('label.live')]], src))}
    ${src === 'live' ? filter('予想の版', chips('ana-mver', [['pre', t('label.version_pre')], ['preview', t('label.version_preview')]], v)) : ''}
    ${src === 'validation' ? html`<div class="hindsight" role="note" style="margin:8px var(--gutter) 0"><div><b>${t('label.backtest')}</b>${t('ana.hindsight')}</div></div>` : ''}
    ${filter('並び', chips('ana-simsort', [['default', '券種 → 点数'], ['roi', '回収率の高い順']], ui.simsort === 'roi' ? 'roi' : 'default'))}
    ${card('買い方ごとの成績', html`${p.from || ''}〜${p.to || ''} <span class="${cls('periodtag', src === 'validation' ? 'periodtag--bt' : 'periodtag--live')}">${src === 'validation' ? '検証' : '実運用'}</span>`, html`<table class="atable"><thead><tr><th>買い方</th><th>件数</th><th>回収率(95%区間)</th></tr></thead><tbody>${rows.map((r) => html`<tr class="${cls(r.level === 'few' && 'is-lown')}"><td>${r.label}${r.level === 'few' ? refTag(r.races) : ''}</td><td class="n">${fmtNumber(r.bet_races ?? r.races)}R</td>${r.level === 'insufficient' || r.roi === null ? html`<td>${insuf(r.bet_races ?? r.races)}</td>` : html`<td class="rate">${fmtRoi(r.roi)}${r.roi_ci95 ? html`<small>(${Math.round(r.roi_ci95[0] * 100)}〜${Math.round(r.roi_ci95[1] * 100)})${r.ci_crosses_100 ? ' ±' : ''}</small>` : ''}</td>`}</tr>`)}</tbody></table>
      <p class="card__note" style="padding:6px 0 0">${t('ana.sim_budget')} 区間が100%をまたぐ行(±)は、${t('warn.ci_crosses_100')}</p>`)}`;
}

// ---------------------------------------------------------------- 仮想の購入者(W-22、spec/11 8〜9章、文言 spec/09 9〜10章)
// どの人も1レース1,000円で固定。並べ替えの操作は置かない(spec/11 8.1 の順のまま)。線は基準を含めて4本まで
export const PERSONA_DEFAULT = ['win_fav', 'boat1', 'app', 'steady'];
export const PERSONA_MAX_LINES = 4;
const pName = (p) => t(`personas.${p.id}.name`) !== `personas.${p.id}.name` ? t(`personas.${p.id}.name`) : p.name;
const pDesc = (p) => t(`personas.${p.id}.desc`) !== `personas.${p.id}.desc` ? t(`personas.${p.id}.desc`) : p.description;

export function personasBlock({ data, ui }) {
  if (data === undefined) return loading;
  if (!data) return '';
  const src = ui.psrc === 'history' ? 'history' : 'live';
  const block = data[src] || { rows: [] };
  const rows = block.rows || [];
  const base = rows.find((r) => r.is_baseline);
  const picked = (ui.ppick || PERSONA_DEFAULT).filter((id) => rows.some((r) => r.id === id));
  const shown = [base].concat(rows.filter((r) => !r.is_baseline && picked.includes(r.id))).filter(Boolean).slice(0, PERSONA_MAX_LINES);
  const others = rows.filter((r) => !r.is_baseline && picked.includes(r.id));
  const full = others.length >= PERSONA_MAX_LINES - 1;
  // 線の見本: 基準は 'base'、それ以外は表示の順に 0〜2
  let slot = 0;
  const slotOf = new Map(shown.map((r) => [r.id, r.is_baseline ? 'base' : slot++]));
  const pts = (r) => (src === 'live' ? (block.by_day_thinned ? r.by_week || [] : r.by_day || []) : r.by_month || []);
  const labelsSrc = pts(shown[0] || rows[0] || {});
  const labels = labelsSrc.map((x) => (x.day ? x.day.slice(5).replace('-', '/') : x.week || x.month));
  const series = shown.map((r) => ({ name: pName(r).replace('(基準)', ''), base: r.is_baseline, values: pts(r).map((x) => (x.cum_profit ?? null)), lown: r.level !== 'normal' }));
  const chart = labels.length >= 2 ? raw(C().lines(series, labels, { label: '仮想の購入者の累計収支' })) : html`<p class="card__note">推移は2${src === 'live' ? '日' : 'か月'}分以上たまると表示します。</p>`;
  const pchips = html`<div class="pchips" role="group" aria-label="表示する人">${rows.map((r) => {
    if (r.is_baseline) return html`<button class="pchip pchip--base" aria-pressed="true" disabled title="基準は常に表示">${raw(C().lineSwatch('base'))}${pName(r)}</button>`;
    const on = picked.includes(r.id);
    return html`<button class="pchip" aria-pressed="${String(on)}" data-action="ana-ppick" data-value="${r.id}" ${raw(!on && full ? 'disabled aria-describedby="pchip-reason"' : '')}>${on ? raw(C().lineSwatch(slotOf.get(r.id))) : ''}${pName(r)}</button>`;
  })}</div><p class="fchip-reason" id="pchip-reason">線を見分けられるよう、基準を含めて${PERSONA_MAX_LINES}人まで同時に表示します。</p>`;
  const roiCell = (r) => {
    if (r.level === 'insufficient' || r.roi === null || r.roi === undefined) return html`<td>${insuf(r.races_bought)}</td>`;
    return html`<td class="rate">${fmtRoi(r.roi)}${r.roi_ci95 ? html`<small>(${Math.round(r.roi_ci95[0] * 100)}〜${Math.round(r.roi_ci95[1] * 100)})${r.roi_ci95[0] < 1 && r.roi_ci95[1] > 1 ? ' ±' : ''}</small>` : ''}</td>`;
  };
  const table = html`<div class="tbl-wrap" data-noswipe><table class="atable ptable"><thead><tr><th>人</th><th>回収率(95%区間)</th><th>的中率</th><th>件数</th><th>最長連敗</th><th>最大の落ち込み</th></tr></thead><tbody>${rows.map((r) => html`<tr class="${cls(r.level === 'few' && 'is-lown')}">
    <td><b>${pName(r)}</b>${r.is_baseline ? html` <span class="basetag">基準</span>` : ''}${r.level === 'few' ? refTag(r.races_bought) : ''}<small class="faint" style="display:block;min-width:12em">${pDesc(r)}</small></td>
    ${roiCell(r)}<td>${r.level === 'insufficient' || r.hit_rate === null ? '—' : fmtPct(r.hit_rate, 0)}</td><td class="n">${fmtNumber(r.races_bought)}R</td><td>${r.max_losing_streak ?? '—'}</td><td>${r.max_drawdown_yen !== undefined && r.max_drawdown_yen !== null ? `−${fmtNumber(r.max_drawdown_yen)}円` : '—'}</td></tr>`)}</tbody></table></div>`;
  const sel = rows.find((r) => r.id === ui.preflect) || rows.find((r) => !r.is_baseline) || rows[0];
  // 振り返り(personas.json の reflections: {loss_breakdown, candidates, texts, explanation})。texts は型で作った文をそのまま出す
  const refl = sel && sel.reflections && typeof sel.reflections === 'object' ? sel.reflections : null;
  const texts = refl && Array.isArray(refl.texts) ? refl.texts : [];
  const cands = refl && Array.isArray(refl.candidates) ? refl.candidates : [];
  const fewCands = cands.filter((c) => c.level && c.level !== 'normal').length;
  const straddle = cands.some((c) => c.ci_crosses_100 || (Array.isArray(c.roi_ci95) && c.roi_ci95[0] < 1 && c.roi_ci95[1] > 1));
  const reflectBody = sel ? html`<div class="reflectbox">
    <label class="field__label" for="reflect-who">人を選ぶ</label>
    <select class="select" id="reflect-who" data-reflect>${rows.map((r) => html`<option value="${r.id}" ${raw(r.id === sel.id ? 'selected' : '')}>${pName(r)}</option>`)}</select>
    <p class="card__note" style="padding:8px 0 0">${t('reflect.intro')}</p>
    ${sel.races_bought < C().RULES.okN ? html`<p class="card__note" style="padding:4px 0 0"><span class="lown-tag"></span> ${t('reflect.few')}</p>` : ''}
    ${src === 'history' ? html`<p class="card__note" style="padding:4px 0 0">振り返りは実運用の欄で表示します。</p>`
      : texts.length ? html`<ol class="reflect__list">${texts.map((x) => html`<li>${x}</li>`)}</ol>
        <p class="card__note" style="padding:6px 0 0">改善の候補 ${cands.length}件${fewCands ? `(うち件数が少なく参考程度のもの ${fewCands}件)` : ''}。${straddle ? t('reflect.ci_note') : ''}${refl.explanation ? ` ${refl.explanation}` : ''}</p>`
        : html`<p>${t('reflect.none')}</p>`}
  </div>` : '';
  return html`
    ${card(t('personas.heading'), '', html`<p>${t('personas.intro')}</p><p><b>${t('personas.no_recommend')}</b></p><p class="card__note" style="padding:4px 0 0">${t('personas.fixed_stake')}</p>`)}
    ${filter('期間', chips('ana-psrc', [['live', t('label.live')], ['history', '過去の参考']], src))}
    ${src === 'live' ? html`<p class="disclaimer" style="margin-top:4px">${t('personas.live')}(${block.from || ''}〜${block.to || ''})。件数が少ないため、全体を参考程度に見てください。</p>`
      : html`<div class="hindsight" role="note" style="margin:8px var(--gutter) 0"><div><b>${t('personas.backtest')}</b>${t('ana.hindsight')}${block.explanation ? ` ${block.explanation}` : ''}</div></div>`}
    ${card('累計収支', '1レース1,000円', html`${pchips}${chart}`)}
    ${card('人ごとの成績', '左右にスクロール', table)}
    ${card(t('reflect.heading'), '', reflectBody)}`;
}
