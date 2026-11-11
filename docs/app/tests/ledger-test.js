// app/ledger.js のテスト(手計算の値と比べる)。ブラウザで実行する
import * as lg from '../ledger.js';
import * as bs from '../betsum.js';
import * as an from '../analytics.js';

const rows = document.getElementById('rows');
const results = [];
function check(name, got, want) {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  results.push(ok);
  const tr = document.createElement('tr');
  tr.innerHTML = `<td>ledger</td><td></td><td class="${ok ? 'pass' : 'fail'}">${ok ? '一致' : '不一致'}</td><td><pre></pre></td>`;
  tr.children[1].textContent = name;
  tr.querySelector('pre').textContent = ok ? JSON.stringify(got) : `期待: ${JSON.stringify(want)}\n結果: ${JSON.stringify(got)}`;
  rows.appendChild(tr);
}

const pay = (list) => ({ trifecta: list, win: [], exacta: [] });
const races = {
  R1: { race_id: 'R1', state: 'finished', result: { order: [1, 2, 3], absent_entries: [], payouts: pay([{ combination: '1-2-3', amount: 2750 }]) } },
  R2: { race_id: 'R2', state: 'finished', result: { order: [4, 5, 6], absent_entries: [], payouts: pay([{ combination: '4-5-6', amount: 9000 }]) } },
  R3: { race_id: 'R3', state: 'finished', result: { order: [1, 3, 5], absent_entries: [2], payouts: pay([{ combination: '1-3-5', amount: 1200 }]) } },
  R4: { race_id: 'R4', state: 'canceled', result: null },
  R5: { race_id: 'R5', state: 'scheduled', result: null },
  R6: { race_id: 'R6', state: 'finished', result: { order: [2, 3, null], absent_entries: [], payouts: pay([{ combination: null, amount: 100, label: '不成立' }]) } },
};
const b = (id, race, comb, amount, t) => ({ id, race_id: race, bet_type: 'trifecta', combination: comb, amount, bought_at: `2026-11-02T10:${t}:00+09:00`, source: 'self' });
const bets = [
  b('a', 'R1', '1-2-3', 200, '01'), b('b', 'R1', '1-3-2', 100, '02'), b('c', 'R2', '1-2-3', 300, '03'),
  b('d', 'R3', '1-2-4', 100, '04'), b('e', 'R3', '1-3-4', 100, '05'), b('f', 'R4', '1-2-3', 500, '06'),
  b('g', 'R5', '1-2-3', 100, '07'), b('h', 'R6', '2-3-1', 200, '08'),
];
const s = lg.summarize(bets, (id) => races[id] || null);
check('払戻の状態', s.rows.map((r) => `${r.bet.id}:${r.status}:${r.payout}`), ['a:hit:5500', 'b:miss:0', 'c:miss:0', 'd:refund:100', 'e:miss:0', 'f:refund:500', 'g:pending:null', 'h:special:200']);
check('投資・払戻・収支(確定分)', [s.stake, s.ret, s.profit], [1500, 6300, 4800]);
check('未確定の投資', s.pendingStake, 100);
check('最大ドローダウン', s.maxDrawdown, 500);
check('連敗(今・最大)', [s.currentStreak, s.maxStreak], [0, 2]);
check('レースごと(新しい順・状態・購入・払戻・点数)', lg.groupByRace(s.rows).map((x) => `${x.race_id}:${x.status}:${x.stake}:${x.ret}:${x.rows.length}`),
  ['R6:special:200:200:1', 'R5:pending:100:0:1', 'R4:refund:500:500:1', 'R3:miss:200:100:2', 'R2:miss:300:0:1', 'R1:hit:300:5500:2']);
check('結果の無いレース(race 不明)は未確定', lg.settleBet(b('x', 'R9', '1-2-3', 100, '09'), null).status, 'pending');
check('予算 80%', lg.budgetWarnings({ day_limit: 1000, month_limit: null, streak_warn: 5 }, { daySpent: 800, monthSpent: 0, currentStreak: 0 }).map((w) => `${w.kind}:${w.level}`), ['day:near']);
check('予算 100%・月・連敗', lg.budgetWarnings({ day_limit: 1000, month_limit: 5000, streak_warn: 3 }, { daySpent: 1200, monthSpent: 4000, currentStreak: 3 }).map((w) => `${w.kind}:${w.level}`), ['day:over', 'month:near', 'streak:over']);
check('予算が未入力なら警告なし', lg.budgetWarnings(lg.normalizeBudget({}), { daySpent: 99999, monthSpent: 99999, currentStreak: 4 }).length, 0);
check('印の正規化(同じ印は1艇)', lg.normalizeMarks({ 1: '◎', 2: '◎', 3: '○', 7: '▲', 4: 'x' }), { 1: '◎', 3: '○' });
const mk = (marks, race) => ({ marks, race: { ...race, prediction: { boats: [{ entry: 1, p_rank: 1 }] } } });
const st = lg.markStats([mk({ 1: '◎', 2: '○', 3: '▲' }, races.R1), mk({ 4: '◎', 5: '○', 6: '▲' }, races.R2), mk({ 3: '◎' }, races.R3), mk({ 1: '◎' }, races.R4)]);
check('印の成績', [st.races, st.honWin, st.honTop2, st.modelWin, st.triRaces, st.triStake, st.triRet], [3, 2, 3, 2, 2, 200, 11750]);
check('Wilson 区間(5/10)', lg.wilson(5, 10).map((x) => Math.round(x * 1000) / 1000), [0.237, 0.763]);

// 買い目合計(手計算: 1/(1/4+1/5+1/20)=2.0)
check('合成オッズ', Math.round(bs.syntheticOdds([4, 5, 20]) * 1000) / 1000, 2);
check('合成オッズ(オッズ欠け)', bs.syntheticOdds([4, null]), null);
check('配分 1,000円 → 500/400/100', bs.allocate([4, 5, 20], 1000), { amounts: [500, 400, 100], total: 1000, minPayout: 2000 });
check('配分できない(合成オッズ1未満)', bs.allocate([1.5, 2.0, 3.0], 1000), null);
check('配分できない(総額が点数×100未満)', bs.allocate([10, 10, 10], 200), null);
check('コピーにオッズが入らない', bs.copyText({ title: '戸田 5R', bets: [{ label: '3連単', combination: '1-2-3', amount: 200 }], footer: '※注記' }), '戸田 5R\n3連単 1-2-3 200円\n計 1点 200円\n※注記');

// 予算の配分(手計算)
const B5 = [{ prob: 0.10, odds: null }, { prob: 0.08, odds: null }, { prob: 0.06, odds: null }, { prob: 0.04, odds: null }, { prob: 0.02, odds: null }];
check('均等 1,200円・5点 → 端数は確率の高い順', bs.allocateBets('equal', B5, 1200).amounts, [300, 300, 200, 200, 200]);
check('上位厚め 900円(重み3/2/1/1/1。余りは重みの大きい順)', bs.allocateBets('top', B5, 900).amounts, [400, 200, 100, 100, 100]);
check('予算が点数×100円未満なら重みの小さい買い目は0円(300円: 10/30×3=1単位、余り2は重みの大きい順)', bs.allocateBets('prob', B5, 300).amounts, [200, 100, 0, 0, 0]);
check('期待値1以上が無いときは配分しない', bs.allocateBets('ev', [{ prob: 0.1, odds: 5 }], 1000).reason, 'no_positive_ev');
check('引数に収支・連敗を受け取る名前が無い', /profit|streak|history|balance|loss/i.test(bs.allocateBets.toString().split(')')[0]), false);
check('確率に比例 1,500円(10:8:6:4:2)', bs.allocateBets('prob', B5, 1500).amounts, [500, 400, 300, 200, 100]);
check('オッズが無いと払戻をそろえる・期待値比例は使えない', [bs.allocateBets('payout', B5, 1000).reason, bs.allocateBets('ev', B5, 1000).reason], ['no_odds', 'no_odds']);
const O3 = [{ prob: 0.3, odds: 4 }, { prob: 0.25, odds: 5 }, { prob: 0.06, odds: 20 }];
const payR = bs.allocateBets('payout', O3, 1000);
check('払戻をそろえる 1,000円 → 500/400/100、どれでも払戻 ≥ 予算', [payR.amounts, payR.amounts.every((a, i) => a * O3[i].odds >= 1000)], [[500, 400, 100], true]);
check('払戻をそろえるのが無理なら避けられない', bs.allocateBets('payout', [{ prob: 0.5, odds: 1.5 }, { prob: 0.4, odds: 2 }], 1000).reason, 'torigami');
// 期待値: 0.3*4=1.2(+0.2)、0.25*5=1.25(+0.25)、0.06*20=1.2(+0.2)、0.1*5=0.5(0円)。予算1,000円・上限400円
const E4 = O3.concat([{ prob: 0.1, odds: 5 }]);
const ev = bs.allocateBets('ev', E4, 1000);
check('期待値に比例(1未満は0円・1点40%まで・合計は予算以内)', [ev.amounts, ev.total <= 1000, Math.max(...ev.amounts) <= 400], [[300, 300, 300, 0], true, true]);
check('期待値に比例(上限40%で頭打ち)', bs.allocateBets('ev', [{ prob: 0.5, odds: 4 }, { prob: 0.01, odds: 101 }], 1000).amounts, [400, 0]);
const all100 = ['equal', 'top', 'prob', 'payout', 'ev'].every((m) => { const r = bs.allocateBets(m, O3, 1000); return !r.ok || (r.amounts.every((a) => a % 100 === 0) && r.total <= 1000); });
check('どの方式も100円単位で予算以内', all100, true);
check('配分のまとめ(どれか的中・払戻の幅・期待される払戻)', (() => { const x = bs.allocationSummary(O3, [500, 400, 100]); return [Math.round(x.anyHit * 100) / 100, x.payoutMin, x.payoutMax, Math.round(x.expected)]; })(), [0.61, 2000, 2000, 1220]);
// 制約: 入力は(方式・このレースの買い目・予算)だけ。収支・連敗を渡しても結果は変わらず、同じ入力なら同じ出力
check('配分関数の引数は3つだけ', bs.allocateBets.length, 3);
check('買い目に収支・連敗の値を混ぜても結果は同じ', JSON.stringify(bs.allocateBets('top', B5.map((b) => ({ ...b, profit: -50000, losing_streak: 9, last_result: 'miss' })), 900)), JSON.stringify(bs.allocateBets('top', B5, 900)));
check('同じ入力なら何度呼んでも同じ出力', [1, 2, 3, 4, 5].map(() => JSON.stringify(bs.allocateBets('ev', E4, 1000))).every((x, i, a) => x === a[0]), true);

// あなたの収支分析(手計算): R1 は 1-2-3 200円的中(5,500円)と 1-3-2 100円、R2 は 300円外れ
const sumA = lg.summarize(bets, (id) => races[id] || null);
const g = an.groupStats(sumA.rows, () => 'all', { reps: 50 });
check('全体の件数(レース・点)・投資・払戻(結果待ちは除く)', [g[0].races, g[0].bets, g[0].stake, g[0].ret], [5, 7, 1500, 6300]);
check('的中率はレース単位(R1 だけ的中 → 1/5)', g[0].hitRate, 0.2);
check('30レース未満は「件数不足」で区間を出さない', [g[0].level, g[0].ci, g[0].hitCi], ['insufficient', null, null]);
const many = []; for (let i = 0; i < 40; i++) many.push({ bet: { race_id: `X${i}`, amount: 100 }, status: i < 10 ? 'hit' : 'miss', payout: i < 10 ? 300 : 0 });
const gm = an.groupStats(many, () => 'k');
check('40レース: 回収率75%・的中率25%・「参考」', [gm[0].roi, gm[0].hitRate, gm[0].level], [0.75, 0.25, 'few']);
check('回収率の区間は点の値を含み、同じ入力なら同じ', [gm[0].ci[0] <= 0.75 && 0.75 <= gm[0].ci[1], JSON.stringify(an.groupStats(many, () => 'k')[0].ci) === JSON.stringify(gm[0].ci)], [true, true]);
check('的中率の区間は Wilson(10/40)', gm[0].hitCi.map((x) => Math.round(x * 1000) / 1000), lg.wilson(10, 40).map((x) => Math.round(x * 1000) / 1000));
check('時間帯の区切り', [an.timeBand(11), an.timeBand(12), an.timeBand(16), an.timeBand(17)], ['〜12時', '12〜17時', '12〜17時', '17時〜']);
check('古い記録は「不明」、ランク無しは「なし」', [an.evRankKey({}), an.evRankKey({ ev_rank: null }), an.evRankKey({ ev_rank: 'A' }), an.allocationKey({}), an.allocationKey({ allocation: 'top' })], ['不明', 'なし', 'ランクA', '不明', '上位厚め']);

const pass = results.filter(Boolean).length;
const el = document.getElementById('summary');
el.textContent = `${pass === results.length ? 'すべて一致' : `${results.length - pass}件 不一致`}: ${pass}/${results.length}`;
el.className = pass === results.length ? 'ok' : 'ng';
window.__testResult = { pass, total: results.length };
