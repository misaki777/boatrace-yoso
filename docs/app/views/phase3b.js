// Phase 3b の表示(spec/10、DA-6、文言 spec/09 7章): 展開予想(W-15)・選手カード(W-16)・モデルの更新履歴(W-17)
import { html, cls } from '../html.js';
import { fmtNumber } from '../format.js';
import { boat, helpBtn, emptyBox } from '../parts.js';
import { t } from '../wording.js';

const pct = (p) => (typeof p === 'number' ? Math.round(p * 100) : 0);
const FEW_N = 10; // 選手カード: 件数10未満は薄く

/** 展開予想のカード。tenkai が無い(過去日)・null(行が無いレース)ならブロックごと出さない */
export function tenkaiCard(prediction) {
  const tk = prediction && prediction.tenkai;
  if (!tk || typeof tk !== 'object') return '';
  const other = (tk.nuki || 0) + (tk.megumare || 0);
  const parts = [['tk-nige', '逃げ', tk.nige], ['tk-sashi', '差し', tk.sashi], ['tk-makuri', 'まくり', tk.makuri], ['tk-makurizashi', 'まくり差し', tk.makurisashi], ['tk-other', 'その他', other]];
  const rows = [['逃げ', tk.nige, true], ['差し', tk.sashi], ['まくり', tk.makuri], ['まくり差し', tk.makurisashi], ['抜き', tk.nuki], ['恵まれ', tk.megumare]];
  return html`<section class="card" aria-label="${t('tenkai.heading')}">
    <div class="card__head"><h2 class="card__title">${t('tenkai.heading')} ${helpBtn('help.tenkai')}</h2><span class="right">${tk.model_id || ''}</span></div>
    <div class="card__body tenkai">
      <div class="tenkai__hero">${boat(tk.course1_entry || 1, { size: 'lg' })}<div class="tenkai__hero-text"><span class="tenkai__hero-label">${t('tenkai.nige', { entry: tk.course1_entry || 1, pct: '' }).replace(/\s*%$/, '')}</span><span class="tenkai__hero-val">${pct(tk.nige)}<small>%</small></span></div></div>
      <div><div class="tkbar" aria-hidden="true">${parts.map(([c, , p]) => html`<i class="${c}" style="--p:${(p || 0).toFixed(3)}"></i>`)}</div>
      <div class="tkbar-labels" aria-hidden="true">${parts.map(([, n, p]) => html`<span style="--p:${(p || 0).toFixed(3)}">${n} ${pct(p)}%</span>`)}</div></div>
      <ul class="tk-rows">${rows.map(([n, p, main]) => html`<li class="${cls('tk-row', main && 'is-main')}"><span class="tk-row__name">${n}</span><div class="pbar" style="--p:${(p || 0).toFixed(3)}"><span class="pbar__fill"></span></div><span class="tk-row__pct">${pct(p)}%</span></li>`)}</ul>
    </div>
    <p class="card__note">${t('tenkai.note')}</p>
  </section>`;
}

const PLACE = (code) => {
  const n = Number(code);
  if (n >= 1 && n <= 6) return { text: String(n), cls: n <= 3 ? `p${n}` : '' };
  if (n === 14) return { text: 'F', cls: 'pf' };
  return { text: '欠', cls: '' };
};
const pctCell = (v, n) => html`<td class="${cls(n < FEW_N && 'few')}">${typeof v === 'number' ? `${Math.round(v * 100)}%` : '—'}</td>`;

/**
 * 選手カードのシート。state: { status: 'loading'|'ok'|'none'|'failed', racer, course(今回の予定コース), techniques }
 */
export function racerSheet({ entry, name, state }) {
  let body;
  if (state.status === 'loading') body = html`<div class="emptybox" role="status">読み込み中…</div>`;
  else if (state.status === 'none') body = html`<div class="emptybox">${t('card.unavailable')}</div>`;
  else if (state.status === 'failed' || !state.racer) body = html`<div class="emptybox" role="alert">${t('card.load_failed')}</div>`;
  else {
    const r = state.racer;
    const tech = state.techniques || {};
    const nat = r.nat || []; const loc = r.loc || [];
    const l = r.l365 || [];
    const rates = r.rates || [];
    body = html`
      <section class="sheet__sec"><h4>基本</h4>
        <div class="tbl-wrap"><table class="tbl"><thead><tr><th class="l"></th><th>勝率</th><th>2連率</th><th>3連率</th></tr></thead><tbody>
          <tr><td class="l">全国</td><td>${nat[0] ?? '—'}</td><td>${nat[1] ?? '—'}%</td><td>${nat[2] ?? '—'}%</td></tr>
          <tr><td class="l">当地</td><td>${loc[0] ?? '—'}</td><td>${loc[1] ?? '—'}%</td><td>${loc[2] ?? '—'}%</td></tr>
        </tbody></table></div>
        <p class="sheet__note">平均ST ${r.avg_st ?? '—'} · F${r.f ?? 0} · L${r.l ?? 0}</p></section>
      <section class="sheet__sec"><h4>今節の着順 <small>R・進入・決まり手</small></h4>
        ${(r.series || []).length ? html`<div class="mini">${r.series.map((x) => { const p = PLACE(x[3]); return html`<span class="${p.cls}" title="${x[0].slice(0, 2)}/${x[0].slice(2)} ${x[1]}R 進入${x[2]}${tech[x[4]] ? ` ${tech[x[4]]}` : ''}">${p.text}</span>`; })}</div>` : html`<p class="sheet__note">今節の出走はまだありません。</p>`}</section>
      <section class="sheet__sec"><h4>コース別 <small>件数・1着率・決まり手</small></h4>
        <div class="tbl-wrap"><table class="cstable tbl"><thead><tr><th class="l">コース</th><th>件数</th><th>1着</th><th>逃げ</th><th>差し</th><th>まくり</th><th>まくり差し</th><th>2着</th><th>平均ST</th></tr></thead><tbody>
          ${(r.course || []).map((c, i) => html`<tr class="${cls(state.course === i + 1 && 'is-plan', c[0] < FEW_N && 'is-lown')}"><td class="l"><span class="course">${i + 1}</span></td><td class="n">${fmtNumber(c[0])}</td>${pctCell(c[1], c[0])}${pctCell(c[2], c[0])}${pctCell(c[3], c[0])}${pctCell(c[4], c[0])}${pctCell(c[5], c[0])}${pctCell(c[6], c[0])}<td>${c[8] ?? '—'}</td></tr>`)}
        </tbody></table></div></section>
      <section class="sheet__sec"><h4>当地(直近365日)</h4>
        <p class="${cls(l[0] < FEW_N && 'few')}">${fmtNumber(l[0] || 0)}走 · 1着 ${pct(l[1])}% · 2連対 ${pct(l[2])}% · 3連対 ${pct(l[3])}%</p></section>
      <section class="sheet__sec"><h4>直近10走</h4>
        ${(r.recent || []).length ? html`<div class="mini">${r.recent.map((x) => { const p = PLACE(x[2]); return html`<span class="${p.cls}" title="${x[0].slice(0, 2)}/${x[0].slice(2)} 場${x[1]}">${p.text}</span>`; })}</div>` : html`<p class="sheet__note">記録がありません。</p>`}</section>
      <section class="sheet__sec"><h4>レース運び</h4>
        <p>フライング率 ${(rates[0] * 100 || 0).toFixed(1)}% · 出遅れ率 ${(rates[1] * 100 || 0).toFixed(1)}% · 前付け率 ${(rates[2] * 100 || 0).toFixed(1)}%</p></section>
      <p class="sheet__note">${t('card.note')}</p>`;
  }
  const sub = state.racer ? [state.racer.rank, state.racer.branch].filter(Boolean).join(' · ') : '';
  return html`<div class="scrim" data-action="card-close"></div>
    <section class="sheet cardsheet" role="dialog" aria-modal="true" aria-labelledby="card-title" tabindex="-1" data-cardsheet>
      <div class="sheet__handle" aria-hidden="true"></div>
      <header class="sheet__head">${boat(entry)}<div><div class="sheet__title" id="card-title">${name}</div><div class="sheet__sub">${t('card.heading')}${sub ? ` · ${sub}` : ''}${state.course ? ` · 今回 ${state.course}コース予定` : ''}</div></div>
        <button class="sheet__close" data-action="card-close" aria-label="閉じる">×</button></header>
      <div class="sheet__body">${body}</div>
    </section>`;
}

/** モデルの更新履歴(S5 から)。採用・不採用を同じ重さで */
export function modelsView({ data, error, appbar, tabbar }) {
  let body;
  if (error) body = emptyBox({ icon: '⚠', title: 'モデルの更新履歴を読み込めませんでした', text: error, role: 'alert' });
  else if (!data) body = html`<div class="emptybox" role="status">読み込み中…</div>`;
  else {
    const hist = Array.isArray(data.history) ? data.history : [];
    body = html`<section class="card" aria-label="${t('models.heading')}">
      <p class="card__note" style="padding-top:12px">${t('models.note')}</p>
      <div class="card__body">${hist.length ? html`<ol class="mhists">${hist.map((h) => {
        const m = h.metrics || {};
        const metric = Object.entries(m).map(([k, v]) => html`<span class="mhist__metric">${k === 'win_logloss' ? '1着 logloss' : k === 'top1_acc' ? '1着的中率' : k === 'ece' ? 'ECE' : k} <b>${v.candidate}</b>(今 ${v.latest})</span> `);
        return html`<li class="${cls('mhist', h.adopted ? 'mhist--adopted' : 'mhist--kept')}"><span class="mhist__date num">${h.date}</span>
          <div class="mhist__head"><span class="mhist__tag">${h.adopted ? '切り替えた' : '切り替えず'}</span><span class="mhist__id">${h.candidate || ''}</span></div>
          <div class="mhist__body">${h.adopted ? '' : html`${t('models.not_adopted')} `}${h.reason || ''}<br><span class="faint">比較に使った月 ${h.valid || '—'} · 学習 〜${h.train_to || '—'}</span><br>${metric}</div></li>`;
      })}</ol>` : html`<p class="card__note">まだ履歴がありません。</p>`}</div>
    </section>`;
  }
  return html`${appbar}${body}${tabbar}`;
}
