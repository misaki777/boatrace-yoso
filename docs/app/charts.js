/*
 * boatrace-yoso グラフ描画(デザイナーB 案 v0.5)。外部ライブラリなし、SVG を文字列で組み立てる。
 * 通常の <script> で読み込むと window.BRCharts に入る(file:// でも動くように ES Modules にしていない)。
 * フロントは docs/ にコピーし、export 文に書き換えて使ってよい。
 * 色はすべて CSS(components.css の .chart / .calib / .pnl)が持つ。ここでは class 名だけを付ける。
 */
(function (global) {
  'use strict';

  /** Wilson スコア区間(95%)。k = 当たり数、n = 件数。[下限, 上限] を 0〜1 で返す */
  function wilson(k, n, z = 1.96) {
    if (!n) return [0, 1];
    const p = k / n, z2 = z * z;
    const c = (p + z2 / (2 * n)) / (1 + z2 / n);
    const h = (z * Math.sqrt(p * (1 - p) / n + z2 / (4 * n * n))) / (1 + z2 / n);
    return [Math.max(0, c - h), Math.min(1, c + h)];
  }

  const esc = s => String(s).replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));

  /**
   * 較正グラフ。bins: [{pred: 予測の平均(0〜1), k: 1着数, n: 件数}]。
   * 目盛りと値を一致させるため、軸は両方とも 0〜100% 固定・20% 刻み。点の位置は (pred, k/n) そのもの。
   * opts.minN(既定 30)未満の帯は「件数が少ない」: 白抜きの点 + 薄い区間。
   */
  function calibration(bins, opts = {}) {
    const W = opts.width || 360, H = opts.height || 300, L = 48, R = 12, T = 12, B = 34, minN = opts.minN ?? 30;
    const x = v => L + v * (W - L - R), y = v => T + (1 - v) * (H - T - B);
    let s = `<svg class="chart calib" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(opts.label || '較正グラフ: 予測の1着確率と実際の1着率')}">`;
    for (let i = 0; i <= 5; i++) {
      const v = i / 5, pct = `${i * 20}%`;
      s += `<line class="grid" x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}"/><line class="grid" x1="${x(v)}" x2="${x(v)}" y1="${T}" y2="${H - B}"/>`;
      s += `<text x="${L - 6}" y="${y(v) + 4}" text-anchor="end">${pct}</text><text x="${x(v)}" y="${H - B + 15}" text-anchor="middle">${pct}</text>`;
    }
    s += `<text class="axis-title" x="${(L + W - R) / 2}" y="${H - 4}" text-anchor="middle">予測の1着確率</text>`;
    s += `<text class="axis-title" x="10" y="${(T + H - B) / 2}" text-anchor="middle" transform="rotate(-90 10 ${(T + H - B) / 2})">実際の1着率</text>`;
    s += `<line class="ideal" x1="${x(0)}" y1="${y(0)}" x2="${x(1)}" y2="${y(1)}"/>`;
    bins.filter(b => b.n > 0).forEach(b => {
      const a = b.k / b.n, [lo, hi] = wilson(b.k, b.n), low = b.n < minN, cx = x(b.pred);
      const tip = `予測 ${(b.pred * 100).toFixed(1)}% / 実際 ${(a * 100).toFixed(1)}%(95%区間 ${(lo * 100).toFixed(0)}〜${(hi * 100).toFixed(0)}%)/ ${b.n}件${low ? '・件数が少ない' : ''}`;
      s += `<g class="bin${low ? ' is-lown' : ''}"><title>${esc(tip)}</title>`;
      s += `<line class="ci" x1="${cx}" x2="${cx}" y1="${y(lo)}" y2="${y(hi)}"/><line class="ci" x1="${cx - 4}" x2="${cx + 4}" y1="${y(lo)}" y2="${y(lo)}"/><line class="ci" x1="${cx - 4}" x2="${cx + 4}" y1="${y(hi)}" y2="${y(hi)}"/>`;
      s += `<circle class="pt" cx="${cx}" cy="${y(a)}" r="4.5"/><rect class="hit" x="${cx - 12}" y="${T}" width="24" height="${H - T - B}"/></g>`;
    });
    return s + '</svg>';
  }

  /**
   * 収支の推移とドローダウン。days: [{label, pnl: その日の収支(円)}]。
   * 累積収支の線、0円の基準線、ピークからの下落(ドローダウン)を帯で塗る。最大ドローダウンに注記。
   */
  function pnl(days, opts = {}) {
    const W = opts.width || 360, H = opts.height || 220, L = 52, R = 14, T = 14, B = 26;
    let cum = 0, peak = 0, maxDd = 0, ddAt = 0, ddPeakAt = 0, curPeakAt = 0;
    const pts = days.map((d, i) => {
      cum += d.pnl;
      if (cum > peak) { peak = cum; curPeakAt = i; }
      if (peak - cum > maxDd) { maxDd = peak - cum; ddAt = i; ddPeakAt = curPeakAt; }
      return {i, cum, peak};
    });
    const lo = Math.min(0, ...pts.map(p => p.cum)), hi = Math.max(0, ...pts.map(p => p.peak));
    const step = niceStep((hi - lo) / 4 || 1000), y0 = Math.floor(lo / step) * step, y1 = Math.ceil(hi / step) * step;
    const x = i => L + (pts.length > 1 ? i * (W - L - R) / (pts.length - 1) : (W - L - R) / 2);
    const y = v => T + (1 - (v - y0) / (y1 - y0 || 1)) * (H - T - B);
    let s = `<svg class="chart pnl" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(opts.label || '累積収支の推移とドローダウン')}">`;
    for (let v = y0; v <= y1 + 1e-9; v += step) s += `<line class="grid" x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}"/><text x="${L - 6}" y="${y(v) + 4}" text-anchor="end">${yen(v)}</text>`;
    // ドローダウンの帯: ピーク線と累積線のあいだ
    const top = pts.map(p => `${x(p.i)},${y(p.peak)}`).join(' '), bot = pts.slice().reverse().map(p => `${x(p.i)},${y(p.cum)}`).join(' ');
    s += `<polygon class="dd" points="${top} ${bot}"/>`;
    s += `<line class="base" x1="${L}" x2="${W - R}" y1="${y(0)}" y2="${y(0)}"/>`;
    s += `<polyline class="peak" points="${top}"/><polyline class="line" points="${pts.map(p => `${x(p.i)},${y(p.cum)}`).join(' ')}"/>`;
    if (maxDd > 0) {
      const px = x(ddAt), pyTop = y(pts[ddPeakAt].peak), pyBot = y(pts[ddAt].cum);
      s += `<line class="dd-mark" x1="${px}" x2="${px}" y1="${pyTop}" y2="${pyBot}"/><text class="dd-lbl" x="${px + 5}" y="${(pyTop + pyBot) / 2 + 4}">最大 −${Math.round(maxDd).toLocaleString('ja-JP')}</text>`;
    }
    const last = pts[pts.length - 1];
    if (last) s += `<circle class="end" cx="${x(last.i)}" cy="${y(last.cum)}" r="4.5"/>`;
    if (days.length) s += `<text x="${L}" y="${H - 6}">${esc(days[0].label)}</text><text x="${W - R}" y="${H - 6}" text-anchor="end">${esc(days[days.length - 1].label)}</text>`;
    s += '</svg>';
    return {svg: s, total: last ? last.cum : 0, maxDrawdown: maxDd};
  }

  function niceStep(raw) {
    const p = Math.pow(10, Math.floor(Math.log10(raw))), f = raw / p;
    return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * p;
  }
  function yen(v) { return (v < 0 ? '−' : v > 0 ? '+' : '') + Math.abs(Math.round(v)).toLocaleString('ja-JP'); }


  // ===================== v0.9 追加(DB-7: 分析画面) =====================
  /** 件数の扱い(spec/11 1.3)。'ok' / 'few'(30〜199: 薄く「参考」)/ 'none'(30未満: 数字を出さず「件数不足」) */
  const RULES = {okN: 200, minN: 30};
  function nClass(n) { return n >= RULES.okN ? 'ok' : n >= RULES.minN ? 'few' : 'none'; }
  const pct = (v, d = 1) => (v * 100).toFixed(d) + '%';

  /**
   * 推移の折れ線。series: [{name, values:[0〜1 or null], n:[件数]}](1〜2本)、labels: x軸の名前(週・月)。
   * opts.marks: [{at: index, label: 'モデル切替'}] → 縦の破線と上の注記。opts.ref: 基準線(例 全国平均 0.55)と opts.refLabel。
   * 件数 < minN の点は線を切り、白抜きの点(few)/点なし(none)。2本目は破線で描く(色だけにしない)。
   * opts.format: 'pct'(既定。0.55 → 55%)/ 'num'(ECE など。opts.digits 桁の小数)。opts.step: 目盛りの間隔(既定は自動)。
   */
  function trend(series, labels, opts = {}) {
    const W = opts.width || 360, H = opts.height || 220, L = 40, R = 14, T = 22, B = 26;
    const all = series.flatMap(s => s.values.filter(v => v != null)).concat(opts.ref != null ? [opts.ref] : []);
    const lo0 = opts.min ?? Math.max(0, Math.floor((Math.min(...all) - .05) * 10) / 10), hi0 = opts.max ?? Math.min(1, Math.ceil((Math.max(...all) + .05) * 10) / 10);
    // ECE など小さい値は opts.min / opts.max / opts.step を指定する(例 min 0, max .08, step .02)
    const x = i => L + (labels.length > 1 ? i * (W - L - R) / (labels.length - 1) : (W - L - R) / 2);
    const y = v => T + (1 - (v - lo0) / (hi0 - lo0 || 1)) * (H - T - B);
    let s = `<svg class="chart trend" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(opts.label || '推移')}">`;
    const step = opts.step || ((hi0 - lo0) > .5 ? .2 : .1);
    const fmt = v => opts.format === 'num' ? v.toFixed(opts.digits ?? 2) : `${Math.round(v * 100)}%`;
    for (let v = lo0; v <= hi0 + 1e-9; v += step) s += `<line class="grid" x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}"/><text x="${L - 6}" y="${y(v) + 4}" text-anchor="end">${fmt(v)}</text>`;
    if (opts.ref != null) s += `<line class="base" x1="${L}" x2="${W - R}" y1="${y(opts.ref)}" y2="${y(opts.ref)}"/><text class="ref-lbl" x="${L + 4}" y="${y(opts.ref) - 4}">${esc(opts.refLabel || '基準')}</text>`;
    (opts.marks || []).forEach(m => { s += `<line class="vmark" x1="${x(m.at)}" x2="${x(m.at)}" y1="${T - 4}" y2="${H - B}"/><text class="vmark-lbl" x="${x(m.at) + 3}" y="${T - 8}">${esc(m.label)}</text>`; });
    series.forEach((sr, si) => {
      let seg = [], segs = [];
      sr.values.forEach((v, i) => { const c = v == null ? 'none' : nClass(sr.n ? sr.n[i] : RULES.okN); if (c === 'none') { if (seg.length) segs.push(seg); seg = []; } else seg.push([i, v]); });
      if (seg.length) segs.push(seg);
      segs.forEach(g => { s += `<polyline class="line${si ? ' line--2' : ''}" points="${g.map(([i, v]) => `${x(i)},${y(v)}`).join(' ')}"/>`; });
      sr.values.forEach((v, i) => { if (v == null) return; const n = sr.n ? sr.n[i] : RULES.okN, c = nClass(n); if (c === 'none') return;
        s += `<circle class="dot${c === 'few' ? ' is-lown' : ''}${si ? ' dot--2' : ''}" cx="${x(i)}" cy="${y(v)}" r="3.5"><title>${esc(`${labels[i]} ${sr.name || ''} ${opts.format === 'num' ? v.toFixed(opts.digits ?? 3) : pct(v)}(${n}件${c === 'few' ? '・参考' : ''})`)}</title></circle>`; });
      const li = sr.values.map((v, i) => v != null && nClass(sr.n ? sr.n[i] : RULES.okN) !== 'none' ? i : -1).filter(i => i >= 0).pop();
      if (li != null && series.length > 1) {
        // 2本の名前が重ならないよう、最後の値が大きい方を線の上、小さい方を線の下に置く
        const other = series[1 - si] && series[1 - si].values[li], below = other != null && sr.values[li] < other;
        s += `<text class="lbl" x="${x(li) - 4}" y="${y(sr.values[li]) + (below ? 15 : -7)}" text-anchor="end">${esc(sr.name)}</text>`;
      }
    });
    s += `<text x="${L}" y="${H - 6}">${esc(labels[0] || '')}</text><text x="${W - R}" y="${H - 6}" text-anchor="end">${esc(labels[labels.length - 1] || '')}</text>`;
    return s + '</svg>';
  }

  /**
   * 横棒の比較(場・コース)。HTML を返す(行ごとに文字が読めるように)。
   * items: [{label, value(0〜1), n, lo?, hi?}]、opts.ref: 全国平均などの基準(縦線と「全国 55%」)。
   * opts.mode: 'bar'(既定)/ 'dot'(区間つきの点)。件数で ok/few/none を切り替え、none は「件数不足」。
   * opts.max: 目盛りの上限(既定 1)。opts.highlight: 強調する label。
   * opts.diff: true のときだけ基準との差(▲▼pt)を出す(既定 false。同じ基準と比べて意味があるときだけ使う)。
   */
  function hbars(items, opts = {}) {
    const max = opts.max || 1, mode = opts.mode || 'bar', ref = opts.ref;
    const rows = items.map(it => {
      const c = nClass(it.n), hl = opts.highlight === it.label ? ' is-hl' : '';
      if (c === 'none') return `<div class="hb-row is-none${hl}"><span class="hb-label">${esc(it.label)}</span><span class="hb-track"></span><span class="hb-val"><span class="insuff">件数不足</span><small>${it.n}件</small></span></div>`;
      const w = v => `${Math.max(0, Math.min(1, v / max)) * 100}%`;
      const ci = it.lo != null ? `<i class="hb-ci" style="left:${w(it.lo)};width:calc(${w(it.hi)} - ${w(it.lo)})"></i>` : '';
      const mark = mode === 'dot' ? `<i class="hb-dot" style="left:${w(it.value)}"></i>` : `<i class="hb-fill" style="width:${w(it.value)}"></i>`;
      const refl = ref != null ? `<i class="hb-ref" style="left:${w(ref)}"></i>` : '';
      const diff = ref != null && opts.diff ? `<small class="diff ${it.value - ref >= 0 ? 'diff--up' : 'diff--down'}">${it.value - ref >= 0 ? '+' : '−'}${Math.abs((it.value - ref) * 100).toFixed(1)}pt</small>` : '';
      return `<div class="hb-row${c === 'few' ? ' is-lown' : ''}${hl}"><span class="hb-label">${esc(it.label)}</span><span class="hb-track">${refl}${ci}${mark}</span><span class="hb-val">${pct(it.value)}<small>${it.n.toLocaleString()}件${c === 'few' ? '・参考' : ''}</small>${diff}</span></div>`;
    }).join('');
    const legend = ref != null ? `<div class="hb-legend"><i class="lg-ref"></i>${esc(opts.refLabel || '全国平均')} ${pct(ref)}${mode === 'dot' || items.some(i => i.lo != null) ? '<i class="lg-ci" style="margin-left:10px"></i>95%区間' : ''}</div>` : '';
    return `<div class="hbars${mode === 'dot' ? ' hbars--dot' : ''}" role="list">${rows}</div>${legend}`;
  }

  /**
   * 積み上げ横棒(決まり手の割合など)。rows: [{label, n, parts:[{key, name, p}]}]。key は tk-nige などの模様のクラス名。
   * 15%以上の区間には中に % を書き、全体の凡例を下に付ける(色と模様の両方で区別)。
   */
  function stacked(rows, opts = {}) {
    const keys = [];
    const body = rows.map(r => {
      const c = nClass(r.n);
      if (c === 'none') return `<div class="sb-row is-none"><span class="hb-label">${esc(r.label)}</span><span class="insuff">件数不足</span><small class="sb-n">${r.n}件</small></div>`;
      const seg = r.parts.map(pt => { if (!keys.find(k => k.key === pt.key)) keys.push(pt); return `<i class="${pt.key}" style="--p:${pt.p}" title="${esc(pt.name)} ${pct(pt.p, 0)}">${pt.p >= .15 ? `<b>${Math.round(pt.p * 100)}</b>` : ''}</i>`; }).join('');
      return `<div class="sb-row${c === 'few' ? ' is-lown' : ''}"><span class="hb-label">${esc(r.label)}</span><span class="tkbar sb-bar">${seg}</span><small class="sb-n">${r.n.toLocaleString()}件</small></div>`;
    }).join('');
    const legend = `<div class="hb-legend">${keys.map(k => `<span><i class="sb-key ${k.key}"></i>${esc(k.name)}</span>`).join('')}<span>(数字は%)</span></div>`;
    return `<div class="stacked">${body}</div>${legend}`;
  }

  /**
   * ヒートマップ(場×コースなど)。HTML の表を返す。cells[r][c] = {v(0〜1), n} / null。
   * 濃さは1色(アクセント)で 0〜50% まで(文字の 4.5:1 を保つため)、**各マスに必ず数字**。
   * 件数 few は斜線の地 + 数字を薄く、none は「–」(件数不足)。行・列の見出しはそのまま th。
   */
  function heatmap(rowLabels, colLabels, cells, opts = {}) {
    const vals = cells.flat().filter(c => c && nClass(c.n) !== 'none').map(c => c.v);
    const lo = opts.min ?? Math.min(...vals), hi = opts.max ?? Math.max(...vals);
    let s = `<table class="heat" aria-label="${esc(opts.label || 'ヒートマップ')}"><thead><tr><th scope="col">${esc(opts.corner || '')}</th>${colLabels.map(c => `<th scope="col">${esc(c)}</th>`).join('')}</tr></thead><tbody>`;
    rowLabels.forEach((rl, r) => {
      s += `<tr><th scope="row">${esc(rl)}</th>`;
      colLabels.forEach((cl, c) => {
        const cell = cells[r][c];
        if (!cell || nClass(cell.n) === 'none') { s += `<td class="is-none" title="${esc(`${rl} ${cl} 件数不足(${cell ? cell.n : 0}件)`)}">–</td>`; return; }
        const t = hi > lo ? (cell.v - lo) / (hi - lo) : .5, few = nClass(cell.n) === 'few';
        s += `<td class="${few ? 'is-lown' : ''}" style="--t:${t.toFixed(3)}" title="${esc(`${rl} ${cl} ${pct(cell.v)}(${cell.n}件${few ? '・参考' : ''})`)}">${Math.round(cell.v * 100)}</td>`;
      });
      s += '</tr>';
    });
    s += '</tbody></table>';
    s += `<div class="hb-legend"><span class="heat-scale"><i style="--t:0"></i><i style="--t:.5"></i><i style="--t:1"></i></span>${Math.round(lo * 100)}% → ${Math.round(hi * 100)}%<span><i class="sb-key heat-few"></i>参考(件数少)</span><span>– 件数不足</span></div>`;
    return s;
  }

  global.BRCharts = {wilson, calibration, pnl, yen, trend, hbars, stacked, heatmap, nClass, RULES};
})(typeof window !== 'undefined' ? window : globalThis);
