// strategy_cases.json の全ケースを app/strategy.js で確かめる(ブラウザで実行する)
// 一致の条件: 推奨の組合せと順序が完全に一致、設定の正規化が一致、
// odds_cases の expected_evaluations は 出どころ・オッズ(丸め直さない)・ランクが一致し、ev の差が 1e-9 未満。
import * as st from '../strategy.js';

const params = new URLSearchParams(location.search);
const src = params.get('cases') || '../../data/sample/strategy_cases.json';
document.getElementById('src').textContent = src;
const rows = document.getElementById('rows');
const results = [];

function add(kind, id, ok, detail = '') {
  results.push({ kind, id, ok, detail });
  const tr = document.createElement('tr');
  tr.innerHTML = `<td>${kind}</td><td>${id}</td><td class="${ok ? 'pass' : 'fail'}">${ok ? '一致' : '不一致'}</td><td><pre></pre></td>`;
  tr.querySelector('pre').textContent = detail;
  rows.appendChild(tr);
}

const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const nul = (v) => (v === undefined ? null : v);

async function main() {
  const res = await fetch(src, { cache: 'no-cache' });
  const data = await res.json();

  add('定数', 'constants', same(st.CONSTANTS, data.constants), same(st.CONSTANTS, data.constants) ? '' : JSON.stringify(st.CONSTANTS));
  add('定数', 'default_settings', same(st.defaultSettings(), data.default_settings));

  for (const c of data.cases) {
    const got = st.recommend(c.race, c.settings);
    add('cases', c.id, same(got, c.expected), same(got, c.expected) ? c.description : `期待: ${JSON.stringify(c.expected)}\n結果: ${JSON.stringify(got)}`);
  }
  for (const c of data.normalize_cases) {
    const got = st.normalizeSettings(c.input);
    // 正規化した設定で計算しても落ちないことも確かめる
    add('normalize_cases', c.id, same(got, c.expected), same(got, c.expected) ? c.description : `期待: ${JSON.stringify(c.expected)}\n結果: ${JSON.stringify(got)}`);
  }
  for (const c of data.odds_cases) {
    const problems = [];
    const got = st.recommend(c.race, c.settings, c.odds_input);
    if (!same(got, c.expected)) problems.push(`推奨 期待: ${JSON.stringify(c.expected)}\n推奨 結果: ${JSON.stringify(got)}`);
    for (const [bt, list] of Object.entries(c.expected_evaluations || {})) {
      const ev = st.evaluatePicks(c.race.picks, bt, c.odds_input);
      if (ev.length !== list.length) problems.push(`${bt}: 件数 ${ev.length} / ${list.length}`);
      list.forEach((e, i) => {
        const g = ev[i] || {};
        if (g.combination !== e.combination) problems.push(`${bt}[${i}] 組合せ ${g.combination} / ${e.combination}(順序)`);
        if (g.source !== e.source) problems.push(`${bt} ${e.combination} 出どころ ${g.source} / ${e.source}`);
        if (nul(g.odds) !== nul(e.odds)) problems.push(`${bt} ${e.combination} オッズ ${g.odds} / ${e.odds}`);
        if (nul(g.ev_rank) !== nul(e.ev_rank)) problems.push(`${bt} ${e.combination} ランク ${g.ev_rank} / ${e.ev_rank}`);
        const ge = nul(g.ev);
        const ee = nul(e.ev);
        if ((ge === null) !== (ee === null) || (ge !== null && !(Math.abs(ge - ee) < 1e-9))) problems.push(`${bt} ${e.combination} ev ${ge} / ${ee}`);
      });
    }
    add('odds_cases', c.id, problems.length === 0, problems.length ? problems.join('\n') : c.description);
  }

  // 追加の確認: ランクの境界・欠損の扱い
  const extra = [
    ['ランク境界 1.2 は A', st.evRankOf(1.2) === 'A'],
    ['ランク境界 1.0 は B', st.evRankOf(1.0) === 'B'],
    ['ランク境界 0.8 は C', st.evRankOf(0.8) === 'C'],
    ['0.7999 は D', st.evRankOf(0.7999) === 'D'],
    ['ev が null ならランクなし', st.evRankOf(null) === null],
    ['ev_rank のキー無しと null は同じ(選び方)', same(
      st.recommend({ confidence: 0.6, picks: { trifecta: [{ combination: '1-2-3', prob: 0.1 }, { combination: '1-3-2', prob: 0.09, ev_rank: null }] } }, { bet_types: { trifecta: 5 }, ev_filter: 'B_or_above', no_odds: 'skip' }),
      [])],
    ['複勝は下限のオッズ', st.workerOddsFor({ place: { 1: { lower_limit: 1.1, upper_limit: 1.3 } } }, 'place', '1').odds === 1.1],
  ];
  extra.forEach(([name, ok]) => add('追加', name, ok));

  const pass = results.filter((r) => r.ok).length;
  const fail = results.length - pass;
  const counts = ['cases', 'normalize_cases', 'odds_cases'].map((k) => `${k} ${results.filter((r) => r.kind === k && r.ok).length}/${results.filter((r) => r.kind === k).length}`).join(' · ');
  const el = document.getElementById('summary');
  el.textContent = `${fail === 0 ? 'すべて一致' : `${fail}件 不一致`}: ${pass}/${results.length}(${counts})`;
  el.className = fail === 0 ? 'ok' : 'ng';
  document.title = `${fail === 0 ? 'OK' : 'NG'} ${pass}/${results.length} 推奨買い目の計算のテスト`;
  window.__testResult = { pass, fail, total: results.length, counts };
}

main().catch((e) => {
  const el = document.getElementById('summary');
  el.textContent = `テストを実行できませんでした: ${e.message}`;
  el.className = 'ng';
  window.__testResult = { pass: 0, fail: -1, error: String(e) };
});
