// S3 成績。design/screens README 6章、design/visual README 4章(推移グラフ)
import { html, raw, cls } from '../html.js';
import { fmtUpdated, todayJst, fmtMonthDay } from '../time.js';
import { fmtRoi, fmtRoiMarked, roiClass, fmtPct, fmtYen, fmtSignedYen, fmtNumber, strategyLabel, sameStrategy } from '../format.js';
import { tabbar, reloadButton, helpBtn } from '../parts.js';
import { t } from '../wording.js';
import '../charts.js';

function appbar({ updatedText, busy }) {
  return html`<header class="appbar">
    <h1 class="appbar__title is-pad">成績</h1>
    ${updatedText ? html`<span class="appbar__updated">${updatedText}</span>` : ''}
    ${reloadButton(busy)}
  </header>`;
}

const periodText = (from, to) => (from && to ? `${fmtMonthDay(from)}〜${fmtMonthDay(to)}` : '');

/** 実運用の回収率 */
function liveCard(live) {
  const t = live.total || {};
  const roi = t.roi;
  const diff = roi !== null && roi !== undefined ? Math.round((roi - 1) * 1000) / 10 : null;
  let delta = '';
  if (diff !== null) {
    delta = diff < 0
      ? html`<span class="delta is-minus">▼ 100%まで あと${Math.abs(diff).toFixed(1)}pt</span>`
      : html`<span class="delta is-plus">▲ 100%超え +${diff.toFixed(1)}pt</span>`;
  }
  const profit = (t.return ?? 0) - (t.stake ?? 0);
  const label = `既定 ${live.strategy_label || strategyLabel(live.strategy)}`;
  return html`<section class="card" aria-label="実運用の回収率">
    <div class="card__head"><h2 class="card__title">実運用の回収率</h2><span class="right">${[periodText(live.from, live.to), label].filter(Boolean).join(' · ')}</span></div>
    <div class="roi-hero">
      <span class="${cls('v', roiClass(roi))}">${roi >= 1 ? '▲' : ''}${roi !== null && roi !== undefined ? (roi * 100).toFixed(1) : '—'}<small>%</small></span>
      ${delta}
    </div>
    <div class="kpi">
      <div><div class="k">的中率(レース)</div><div class="v">${fmtPct(t.hit_rate)}</div></div>
      <div><div class="k">レース数 / 点数</div><div class="v">${fmtNumber(t.races)}R / ${fmtNumber(t.bets)}点</div></div>
      <div><div class="k">投資(1点100円)</div><div class="v">${fmtYen(t.stake)}</div></div>
      <div><div class="k">払戻</div><div class="v">${fmtYen(t.return)}</div></div>
      <div class="wide"><div class="k">収支</div><div class="${cls('v', profit >= 0 ? 'is-plus' : 'is-minus')}">${fmtSignedYen(profit)}</div></div>
    </div>
  </section>`;
}

/**
 * 累積回収率の推移(SVG、viewBox 320×160)。
 * Y軸は 50〜150% を基本に、外れたら25%刻みで広げる(デザイナーA)。100%に点線、最新点に丸とラベル。
 */
export function chartSvg(byDay) {
  const W = 320;
  const H = 160;
  const left = 38;
  const right = 312;
  const top = 10;
  const bottom = 140;
  const values = byDay.map((d) => d.cum_roi);
  let min = 0.5;
  let max = 1.5;
  values.forEach((v) => {
    if (typeof v !== 'number') return;
    if (v < min) min = Math.floor(v / 0.25) * 0.25;
    if (v > max) max = Math.ceil(v / 0.25) * 0.25;
  });
  min = Math.max(0, min);
  const x = (i) => (byDay.length === 1 ? (left + right) / 2 : left + ((right - left) * i) / (byDay.length - 1));
  const y = (v) => bottom - ((bottom - top) * (v - min)) / (max - min);
  const step = max - min > 1.5 ? 0.5 : 0.25;
  const grid = [];
  for (let v = min; v <= max + 1e-9; v += step) {
    const yy = y(v).toFixed(1);
    if (Math.abs(v - 1) > 1e-9) grid.push(`<line class="grid" x1="${left}" x2="${right}" y1="${yy}" y2="${yy}"/>`);
    if (Math.abs((v * 100) % 50) < 1e-6 || Math.abs((v * 100) % 50 - 50) < 1e-6) {
      grid.push(`<text x="${left - 6}" y="${(y(v) + 3.5).toFixed(1)}" text-anchor="end">${Math.round(v * 100)}%</text>`);
    }
  }
  const points = values.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ');
  const last = values.length - 1;
  const lx = x(last);
  const ly = y(values[last]);
  const mid = Math.floor(last / 2);
  const xLabels = [`<text x="${x(0).toFixed(1)}" y="156">${fmtMonthDay(byDay[0].date)}</text>`];
  if (last >= 2) xLabels.push(`<text x="${x(mid).toFixed(1)}" y="156" text-anchor="middle">${fmtMonthDay(byDay[mid].date)}</text>`);
  if (last >= 1) xLabels.push(`<text x="${x(last).toFixed(1)}" y="156" text-anchor="end">${fmtMonthDay(byDay[last].date)}</text>`);
  const labelY = ly - 9 < top + 4 ? ly + 16 : ly - 9;
  const svg = `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="累積回収率の推移。最新 ${fmtRoi(values[last])}" data-chart>
    ${grid.join('')}
    <line class="base" x1="${left}" x2="${right}" y1="${y(1).toFixed(1)}" y2="${y(1).toFixed(1)}"/>
    <polyline class="line" points="${points}"/>
    <line class="cursor" x1="0" x2="0" y1="${top}" y2="${bottom}" visibility="hidden" data-cursor/>
    <circle class="end" cx="${lx.toFixed(1)}" cy="${ly.toFixed(1)}" r="4"/>
    <text class="lbl" x="${(lx - 4).toFixed(1)}" y="${labelY.toFixed(1)}" text-anchor="end">${fmtRoi(values[last])}</text>
    ${xLabels.join('')}
    <rect class="hit-area" x="${left - 10}" y="0" width="${right - left + 20}" height="${bottom + 4}"/>
  </svg>`;
  return { svg, geometry: { left, right, top, bottom, width: W, count: byDay.length } };
}

function chartCard(live) {
  const byDay = (live && live.by_day) || [];
  if (byDay.length < 3) {
    return html`<section class="card" aria-label="累積回収率の推移">
      <div class="card__head"><h2 class="card__title">累積回収率の推移</h2></div>
      <div class="empty empty--dashed"><div class="empty__text" style="margin:0">グラフは3日分以上たまると表示します。</div></div>
    </section>`;
  }
  const { svg } = chartSvg(byDay);
  return html`<section class="card" aria-label="累積回収率の推移">
    <div class="card__head"><h2 class="card__title">累積回収率の推移</h2><span class="right">点線 = 100% · タップで日付ごとの値</span></div>
    <div class="card__body"><div class="chartbox">${raw(svg)}<div class="chart-tip" hidden data-chart-tip></div></div></div>
  </section>`;
}

function monthCard(live) {
  const months = ((live && live.by_month) || []).slice().sort((a, b) => (a.month < b.month ? 1 : -1));
  if (!months.length) return '';
  return html`<section class="card" aria-label="月別">
    <div class="card__head"><h2 class="card__title">月別</h2></div>
    <div class="card__body" style="padding:8px 0 4px">
      <div class="tbl-wrap"><table class="tbl">
        <thead><tr><th>月</th><th>R数</th><th>投資</th><th>払戻</th><th>回収率</th></tr></thead>
        <tbody>${months.map((m) => html`<tr><td>${m.month}</td><td>${fmtNumber(m.races)}</td><td>${fmtNumber(m.stake)}</td><td>${fmtNumber(m.return)}</td><td class="${roiClass(m.roi)}"><b>${fmtRoiMarked(m.roi)}</b></td></tr>`)}</tbody>
      </table></div>
    </div>
  </section>`;
}

function backtestCard(backtest, live, adoptedStrategy) {
  if (!backtest || !Array.isArray(backtest.strategies) || !backtest.strategies.length) {
    return html`<section class="card" aria-label="バックテスト">
      <div class="card__head"><h2 class="card__title">バックテスト</h2></div>
      <p class="card__note" style="padding-top:8px">まだありません(モデルの検証が済むと表示します)。</p>
    </section>`;
  }
  const scaleMax = 2;
  const pos = (v) => `${(Math.max(0, Math.min(scaleMax, v)) / scaleMax) * 100}%`;
  const rows = backtest.strategies.map((s) => {
    const ref = s.leak_free === false;
    const adopted = sameStrategy(s, adoptedStrategy || (live && live.strategy));
    const [lo, hi] = Array.isArray(s.roi_ci95) ? s.roi_ci95 : [null, null];
    const notes = [adopted ? '採用中' : '', ref ? '確定オッズで選んだ値' : '', `的中率 ${fmtPct(s.hit_rate, 0)}`, lo !== null ? `95%区間 ${fmtRoi(lo, 0)}〜${fmtRoi(hi, 0)}` : ''].filter(Boolean).join(' · ');
    const clipped = hi !== null && hi > scaleMax;
    return html`<div class="${cls('ci-row', ref && 'is-ref')}">
      <div class="name">${s.short_label || strategyLabel(s)}${ref ? html`<span class="chip chip--ref">参考値(後知恵)</span>` : ''}<small>${notes}</small></div>
      <div class="${cls('roi', roiClass(s.roi))}">${fmtRoiMarked(s.roi, 0)}</div>
      <div class="ci-track" aria-hidden="true"><span class="axis"></span><span class="base100" style="left:${pos(1)}"></span>${lo !== null ? html`<span class="range" style="left:${pos(lo)};width:calc(${pos(hi)} - ${pos(lo)})"></span>` : ''}<span class="pt" style="left:${pos(s.roi)}"></span>${clipped ? html`<span style="position:absolute;right:-2px;top:0;font-size:12px;color:var(--c-text-3)">›</span>` : ''}</div>
    </div>`;
  });
  const period = backtest.period ? periodText(backtest.period.from, backtest.period.to) : '';
  return html`<section class="card" aria-label="バックテスト">
    <div class="card__head"><h2 class="card__title">バックテスト</h2><span class="right">${period}</span></div>
    <p class="card__note" style="padding-top:4px;padding-bottom:4px">回収率と95%区間。点線 = 100%</p>
    ${rows}
    <div class="ci-scale"><span>0%</span><span>100%</span><span>200%</span></div>
  </section>`;
}

function modelCard(backtest, dayModel) {
  if (!backtest) return '';
  const m = backtest.metrics || {};
  const lines = [];
  if (dayModel) {
    lines.push(`種類: ${dayModel.variant === 'preview' ? '直前モデル(出走表+直前情報)' : '前日モデル(出走表のみ)'}${dayModel.train_until ? ` · 学習: 〜${dayModel.train_until}` : ''}`);
  }
  if (backtest.period) lines.push(`検証期間: ${backtest.period.from}〜${backtest.period.to}`);
  const metrics = [];
  if (m.top1_acc !== undefined) metrics.push(`1着的中率 ${fmtPct(m.top1_acc)}`);
  if (m.trifecta_top10_hit !== undefined) metrics.push(`3連単上位10点的中 ${fmtPct(m.trifecta_top10_hit)}`);
  if (m.win_logloss !== undefined) metrics.push(`logloss ${m.win_logloss.toFixed(3)}`);
  if (metrics.length) lines.push(metrics.join(' · '));
  return html`<section class="card">
    <details class="fold" data-fold="model">
      <summary>モデルの情報 <span class="count">${backtest.model_id || ''}</span></summary>
      <div class="prose prose--small"><p>${lines.map((l, i) => html`${i ? raw('<br>') : ''}${l}`)}</p></div>
    </details>
  </section>`;
}

/** S3 本体 */
/** 設定が既定と違うときの注記(design/screens README 12.5) */
function customBanner(perf, customText) {
  if (!customText) return '';
  const def = (perf.live && (perf.live.strategy_label || strategyLabel(perf.live.strategy))) || '既定の買い方';
  return html`<div class="banner banner--info banner--stack" role="note"><span class="banner__icon" aria-hidden="true">ℹ</span>
    <div class="banner__body"><b class="banner__title">成績は既定の買い方で集計しています</b><span class="banner__text">既定: ${def} · 1点100円 / 今の設定: ${customText}</span></div>
    <div class="banner__actions"><a class="btn btn--small" href="#/settings">設定</a></div></div>`;
}

const FEW_RACES = 200; // これ未満は薄く表示(spec/08 4章-1)

/** 回収率 + 95%区間(B の .ci-num)。区間が100%をまたぐと色を付けず「プラスともマイナスとも言えない」 */
function ciNum({ label, roi, ci, n, unit = 'R', extra = '' }) {
  if (roi === null || roi === undefined) return '';
  const straddle = Array.isArray(ci) && ci[0] < 1 && ci[1] > 1;
  const low = n !== undefined && n < FEW_RACES;
  const max = Math.max(1.5, Math.ceil(((ci && ci[1]) || roi) * 2) / 2);
  return html`<div class="${cls('ci-num', straddle && 'is-straddle', low && 'is-lown')}">
    <span class="ci-num__label">${label}</span>
    <span class="${cls('ci-num__val', !straddle && !low && roiClass(roi))}">${fmtRoi(roi)}</span>
    ${ci ? html`<span class="ci-num__bar" style="--lo:${ci[0]};--hi:${ci[1]};--v:${roi};--ref:1;--max:${max}" aria-hidden="true"></span><span class="ci-num__ci">95%区間 ${fmtRoi(ci[0], 0)}〜${fmtRoi(ci[1], 0)}</span>` : ''}
    ${straddle ? html`<span class="ci-num__verdict">${t('warn.ci_crosses_100')}</span>` : ''}
    <span class="ci-num__n">${n !== undefined ? `${fmtNumber(n)}${unit}` : ''}${extra}${low ? html` <span class="lown-tag"></span>` : ''}</span>
  </div>`;
}

const ciInline = (roi, ci) => html`<span class="${cls('ci-inline', Array.isArray(ci) && ci[0] < 1 && ci[1] > 1 && 'is-straddle')}">${fmtRoi(roi)}${ci ? html`<small>(${Math.round(ci[0] * 100)}〜${Math.round(ci[1] * 100)})</small>` : ''}</span>`;

/** 較正グラフ(B の BRCharts.calibration)と同じ値の表 */
function calibrationCard(bins, { title, note }) {
  if (!Array.isArray(bins) || !bins.length) return '';
  const data = bins.map((b) => ({ pred: b.pred_mean, k: Math.round(b.actual * b.n), n: b.n }));
  const svg = window.BRCharts ? window.BRCharts.calibration(data, { minN: 30 }) : '';
  const over = bins.find((b) => b.n >= 30 && b.pred_mean - b.actual >= 0.1);
  return html`<section class="card" aria-label="${title}">
    <div class="card__head"><h2 class="card__title">${title} ${helpBtn('help.calibration')}</h2><span class="right">${note}</span></div>
    <div class="card__body">${raw(svg)}</div>
    ${over ? html`<p class="card__note">${t('warn.overconfident', { pred: Math.round(over.pred_mean * 100), actual: Math.round(over.actual * 100) })}</p>` : ''}
    <div class="tbl-wrap"><table class="tbl"><thead><tr><th class="l">予測の帯</th><th>件数</th><th>予測の平均</th><th>実際(95%区間)</th></tr></thead>
    <tbody>${bins.map((b) => html`<tr class="${cls(b.n < 30 && 'is-lown')}"><td class="l">${b.bin.replace('-', '〜')}%</td><td>${fmtNumber(b.n)}</td><td>${fmtPct(b.pred_mean, 1)}</td><td><span class="ci-inline">${fmtPct(b.actual, 1)}${b.ci95 ? html`<small>(${Math.round(b.ci95[0] * 100)}〜${Math.round(b.ci95[1] * 100)})</small>` : ''}</span>${b.n < 30 ? html` <span class="lown-tag"></span>` : ''}</td></tr>`)}</tbody></table></div>
    <p class="card__note" style="padding-top:8px">点が斜めの点線に近いほど、確率がよく合っています。縦の線は95%区間、白抜きは件数30未満。</p>
  </section>`;
}

/** 戦略比較表(基準2種を含む) */
function strategiesCard(list, { title, hindsight }) {
  if (!Array.isArray(list) || !list.length) return '';
  return html`<section class="card" aria-label="${title}">
    <div class="card__head"><h2 class="card__title">${title} ${helpBtn('help.baseline')}</h2><span class="right">回収率(95%区間)</span></div>
    ${hindsight ? html`<p class="card__note" style="padding-top:4px">${t('warn.hindsight')}</p>` : ''}
    <div class="tbl-wrap"><table class="tbl"><thead><tr><th class="l">買い方</th><th>件数</th><th>回収率 ${helpBtn('help.confidence_interval')}</th><th>的中率</th><th>最大連敗 ${helpBtn('help.losing_streak')}</th><th>最大損失 ${helpBtn('help.drawdown')}</th></tr></thead>
    <tbody>${list.map((x) => html`<tr class="${cls((x.races ?? FEW_RACES) < FEW_RACES && 'is-lown')}"><td class="l">${x.short_label || strategyLabel(x)}${x.is_baseline ? html` <span class="chip chip--pre">基準</span>` : ''}${x.leak_free === false ? html` <span class="chip chip--ref">後知恵</span>` : ''}</td><td>${x.races !== undefined ? `${fmtNumber(x.races)}R` : '—'}${x.bets !== undefined ? html`<br><small class="faint">${fmtNumber(x.bets)}点</small>` : ''}</td><td>${ciInline(x.roi, x.roi_ci95)}</td><td>${fmtPct(x.hit_rate, 0)}</td><td>${x.max_losing_streak ?? '—'}</td><td>${x.max_drawdown_yen !== undefined ? fmtYen(x.max_drawdown_yen) : '—'}</td></tr>`)}</tbody></table></div>
    <p class="card__note" style="padding-top:8px">${t('notice.excluded_races')} 件数が200R未満の行は薄く表示しています。</p>
  </section>`;
}

function versionCard(live) {
  const v = live.by_version;
  if (!v) return '';
  const rows = [['pre', t('label.version_pre')], ['preview', t('label.version_preview')]].map(([k, name]) => {
    const x = v[k];
    return html`<tr class="${cls(x && x.races < FEW_RACES && 'is-lown')}"><td class="l">${name}</td>${x ? html`<td>${fmtNumber(x.races)}R</td><td>${ciInline(x.roi, x.roi_ci95)}</td><td>${fmtPct(x.hit_rate, 0)}</td>` : html`<td colspan="3" class="faint">まだありません</td>`}</tr>`;
  });
  const models = Array.isArray(live.models) ? live.models : [];
  return html`<section class="card" aria-label="予想の版ごと">
    <div class="card__head"><h2 class="card__title">予想の版ごと ${helpBtn('help.prediction_version')}</h2></div>
    <div class="tbl-wrap"><table class="tbl"><thead><tr><th class="l">版</th><th>件数</th><th>回収率(95%区間)</th><th>的中率</th></tr></thead><tbody>${rows}</tbody></table></div>
    ${models.length ? html`<p class="card__note" style="padding-top:8px">使ったモデル: ${models.map((m) => `${m.model_id}(${m.from}〜${m.to})`).join('、')}</p>` : ''}
  </section>`;
}

function backtestMonths(bt) {
  const months = Array.isArray(bt.by_month) ? bt.by_month : [];
  if (!months.length) return '';
  return html`<section class="card" aria-label="検証の月別"><div class="card__head"><h2 class="card__title">月別(検証)</h2></div>
    <div class="tbl-wrap"><table class="tbl"><thead><tr><th class="l">月</th><th>件数</th><th>回収率(95%区間)</th></tr></thead>
    <tbody>${months.map((m) => html`<tr class="${cls(m.races < FEW_RACES && 'is-lown')}"><td class="l">${m.month}</td><td>${fmtNumber(m.races)}R</td><td>${ciInline(m.roi, m.roi_ci95)}</td></tr>`)}</tbody></table></div></section>`;
}

/** S3 本体(実運用 / 検証 のタブ) */
export function performanceView({ perf, busy, banners, adoptedStrategy, dayModel, customText, tab = 'live' }) {
  const live = perf.live;
  const bt = perf.backtest;
  const few = live && live.by_day && live.by_day.length < 3;
  const adopted = live && Array.isArray(live.strategies) ? live.strategies.find((x) => !x.is_baseline) : null;
  const tabs = html`<div class="seg" role="tablist" aria-label="実運用と検証">
    <button role="tab" data-action="perf-tab" data-tab="live" aria-selected="${String(tab !== 'backtest')}">${t('label.live')}</button>
    <button role="tab" data-action="perf-tab" data-tab="backtest" aria-selected="${String(tab === 'backtest')}">${t('label.backtest')}</button>
  </div>`;
  const liveBody = html`
    ${live && live.sample_note === 'few' ? html`<div class="banner banner--warn" role="note"><span class="banner__icon">!</span><div class="banner__body"><span class="banner__title">${t('warn.few_samples', { n: fmtNumber((live.total && live.total.races) || 0) })}</span></div></div>` : ''}
    ${live && live.total ? liveCard(live) : html`<section class="card"><div class="card__head"><h2 class="card__title">実運用の回収率</h2></div><p class="card__note" style="padding-top:8px">まだありません(結果が出ると表示します)。</p></section>`}
    ${adopted ? html`<section class="card"><div class="card__body">${ciNum({ label: `回収率と95%区間(${adopted.short_label || ''})`, roi: adopted.roi, ci: adopted.roi_ci95, n: adopted.races, extra: adopted.bets !== undefined ? `・${fmtNumber(adopted.bets)}点` : '' })}</div></section>` : ''}
    ${chartCard(live)}
    ${live ? calibrationCard(live.calibration, { title: '較正(実運用)', note: '1着確率' }) : ''}
    ${live ? strategiesCard(live.strategies, { title: '買い方の比較(実運用)', hindsight: false }) : ''}
    ${live ? versionCard(live) : ''}
    ${monthCard(live)}`;
  const btBody = bt ? html`
    <div class="banner banner--warn" role="note"><span class="banner__icon">!</span><div class="banner__body"><b class="banner__title">${t('label.backtest')} ${helpBtn('help.backtest_vs_live')}</b><span class="banner__text">${t('warn.hindsight')}</span></div></div>
    ${backtestCard(bt, live, adoptedStrategy)}
    ${calibrationCard(bt.calibration, { title: '較正(検証)', note: bt.period ? `${bt.period.from}〜${bt.period.to}` : '' })}
    ${strategiesCard(bt.strategies, { title: '買い方の比較(検証)', hindsight: true })}
    ${backtestMonths(bt)}
    ${modelCard(bt, dayModel)}` : html`<section class="card"><p class="card__note" style="padding-top:8px">検証の成績はまだありません。</p></section>`;
  return html`
    ${appbar({ updatedText: `更新 ${fmtUpdated(perf.generated_at, todayJst())}`, busy })}
    ${banners}
    ${customBanner(perf, customText)}
    ${tabs}
    ${tab === 'backtest' ? btBody : liveBody}
    <section class="card"><a class="linkrow linkrow--first" href="#/marks">自分の予想の成績 <small>印の成績とモデルとの比較</small></a></section>
    <p class="disclaimer"><b>${t('notice.simulation')}</b>${t('notice.excluded_races')}${few ? '少ない日数の回収率は大きくぶれます。' : ''}</p>
    ${tabbar('performance')}`;
}

export function performanceSkeleton() {
  return html`
    ${appbar({ updatedText: '読み込み中…', busy: true })}
    <section class="card" aria-busy="true"><div class="card__body"><span class="sk" style="width:40%;height:16px"></span><span class="sk" style="width:50%;height:40px;margin-top:12px"></span><span class="sk" style="height:80px;margin-top:12px"></span></div></section>
    <section class="card"><div class="card__body"><span class="sk" style="width:40%;height:16px"></span><span class="sk" style="height:140px;margin-top:12px"></span></div></section>
    <span class="sr-only" role="status">読み込み中</span>
    ${tabbar('performance')}`;
}

export function performanceErrorView({ busy, box }) {
  return html`${appbar({ busy })}${box}${tabbar('performance')}`;
}
