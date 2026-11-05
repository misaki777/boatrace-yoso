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

  global.BRCharts = {wilson, calibration, pnl, yen};
})(typeof window !== 'undefined' ? window : globalThis);
