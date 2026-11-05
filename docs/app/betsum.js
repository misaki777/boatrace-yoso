// 買い目合計(spec/08 5.5。DOM を触らない純粋な関数。テスト: docs/app/tests/ledger.html)
// - 合成オッズ = 1 / Σ(1 / odds_i)(同じ金額で買う場合)
// - トリガミ回避の配分: 総額は変えず(増額の提案はしない)、金額_i ∝ 1/odds_i で100円単位に配る。
//   どれが当たっても払戻 ≥ 総額 にならなければ null(「避けられない」と表示する)
// - コピーする文にはオッズ・期待値の数値を入れない

export function syntheticOdds(odds) {
  if (!odds.length || odds.some((o) => typeof o !== 'number' || !(o > 0))) return null;
  return 1 / odds.reduce((a, o) => a + 1 / o, 0);
}

/** 総額 total(100円単位)を odds の逆数に比例して配る。各100円以上。満たせなければ null */
export function allocate(odds, total) {
  const syn = syntheticOdds(odds);
  if (syn === null || !(total >= odds.length * 100)) return null;
  const inv = odds.map((o) => 1 / o);
  const sum = inv.reduce((a, b) => a + b, 0);
  const units = total / 100;
  // まず切り捨てで配り、残りを「足りない度合い」の大きい順に1単位ずつ足す(総額をちょうど保つ)
  const raw = inv.map((v) => (units * v) / sum);
  const amt = raw.map((r) => Math.max(1, Math.floor(r)));
  let left = units - amt.reduce((a, b) => a + b, 0);
  const order = raw.map((r, i) => [r - Math.floor(r), i]).sort((a, b) => b[0] - a[0]).map((x) => x[1]);
  for (let j = 0; left > 0 && j < order.length * 4; j++, left--) amt[order[j % order.length]] += 1;
  if (left < 0) return null;
  const amounts = amt.map((u) => u * 100);
  const spent = amounts.reduce((a, b) => a + b, 0);
  const ok = amounts.every((a, i) => a * odds[i] >= spent);
  return ok ? { amounts, total: spent, minPayout: Math.min(...amounts.map((a, i) => Math.floor(a * odds[i]))) } : null;
}

/** コピーする文(オッズの数値は入れない) */
export function copyText({ title, bets, footer }) {
  const lines = [title];
  bets.forEach((b) => lines.push(`${b.label} ${b.combination} ${b.amount.toLocaleString('ja-JP')}円`));
  lines.push(`計 ${bets.length}点 ${bets.reduce((a, b) => a + b.amount, 0).toLocaleString('ja-JP')}円`);
  if (footer) lines.push(footer);
  return lines.join('\n');
}

// ---------------------------------------------------------------- 予算の配分(利用者の指示による制約)
// 配分は「このレースの予算・確率・オッズ」だけから決める。過去の収支・連敗・前のレースの結果は入力に取らない。
// 負けたあとに金額を増やす仕組みは持たない。同じ入力なら常に同じ出力(乱数・時刻を使わない)。

export const ALLOCATION_METHODS = ['equal', 'top', 'prob', 'payout', 'ev'];
/** オッズが要る配分 */
export const NEEDS_ODDS = ['payout', 'ev'];
const EV_CAP = 0.4; // 期待値に比例: 1点あたり予算の40%まで

/** 重みに比例して units(100円の個数)を配る(spec/08 5.6)。切り捨てて、余りは重みの大きい買い目から1単位ずつ(同じ重みなら確率の高い順) */
function proportional(weights, units, probOrder) {
  const sum = weights.reduce((a, b) => a + b, 0);
  if (!(sum > 0)) return weights.map(() => 0);
  const out = weights.map((w) => Math.floor((units * w) / sum));
  let left = units - out.reduce((a, b) => a + b, 0);
  const order = weights.map((w, i) => i).filter((i) => weights[i] > 0).sort((a, b) => weights[b] - weights[a] || probOrder.indexOf(a) - probOrder.indexOf(b));
  for (let j = 0; left > 0 && order.length; j++, left--) out[order[j % order.length]] += 1;
  return out;
}

/** 上位厚めの重み(確率の順位ごと。4位以下は1) */
export const TOP_WEIGHTS = Object.freeze([3, 2]);

/**
 * 買い目ごとの金額を決める。
 * method: equal / top / prob / payout / ev、bets: [{prob, odds}](odds は無ければ null)、budget: 円(100円単位)。
 * 戻り値: { amounts: [円], total, ok, reason }。ok=false のときは amounts が空(オッズが無い・トリガミを避けられない)
 */
export function allocateBets(method, bets, budget) {
  const n = bets.length;
  const units = Math.floor(Math.max(0, Number(budget) || 0) / 100);
  if (!n) return { amounts: [], total: 0, ok: false, reason: 'no_bets' };
  if (!units) return { amounts: bets.map(() => 0), total: 0, ok: false, reason: 'no_budget' };
  const probOrder = bets.map((b, i) => i).sort((a, b) => bets[b].prob - bets[a].prob || a - b);
  const odds = bets.map((b) => (typeof b.odds === 'number' && b.odds > 0 ? b.odds : null));
  const hasOdds = odds.every((o) => o !== null);
  let u;
  if (method === 'equal') {
    u = proportional(bets.map(() => 1), units, probOrder);
  } else if (method === 'top') {
    const w = bets.map(() => 1);
    TOP_WEIGHTS.forEach((wt, r) => { if (probOrder[r] !== undefined) w[probOrder[r]] = wt; });
    u = proportional(w, units, probOrder);
  } else if (method === 'prob') {
    u = proportional(bets.map((b) => Math.max(0, b.prob || 0)), units, probOrder);
  } else if (method === 'payout') {
    if (!hasOdds) return { amounts: [], total: 0, ok: false, reason: 'no_odds' };
    const a = allocate(odds, units * 100);
    if (!a) return { amounts: [], total: 0, ok: false, reason: 'torigami' };
    return { amounts: a.amounts, total: a.total, ok: true, reason: null };
  } else if (method === 'ev') {
    if (!hasOdds) return { amounts: [], total: 0, ok: false, reason: 'no_odds' };
    const w = bets.map((b, i) => Math.max(0, b.prob * odds[i] - 1));
    const cap = Math.floor(units * EV_CAP);
    const sum = w.reduce((a, b) => a + b, 0);
    if (!(sum > 0)) return { amounts: bets.map(() => 0), total: 0, ok: false, reason: 'no_positive_ev' };
    u = w.map((x) => Math.floor((units * x) / sum));
    const capped = u.some((x) => x > cap);
    u = u.map((x) => Math.min(cap, x));
    const amountsEv = u.map((x) => x * 100);
    return { amounts: amountsEv, total: amountsEv.reduce((a, b) => a + b, 0), ok: true, reason: null, capped };
  } else {
    return { amounts: [], total: 0, ok: false, reason: 'unknown' };
  }
  const amounts = u.map((x) => x * 100);
  return { amounts, total: amounts.reduce((a, b) => a + b, 0), ok: true, reason: null, short: units < n };
}

/** 配分の結果のまとめ: どれかが当たる確率、払戻の幅、期待される払戻(オッズがあるとき) */
export function allocationSummary(bets, amounts) {
  const anyHit = bets.reduce((a, b, i) => a + (amounts[i] > 0 ? b.prob : 0), 0);
  const withOdds = bets.every((b) => typeof b.odds === 'number');
  if (!withOdds) return { anyHit, payoutMin: null, payoutMax: null, expected: null };
  const pays = bets.map((b, i) => Math.floor(amounts[i] * b.odds)).filter((p, i) => amounts[i] > 0);
  return {
    anyHit,
    payoutMin: pays.length ? Math.min(...pays) : 0,
    payoutMax: pays.length ? Math.max(...pays) : 0,
    expected: bets.reduce((a, b, i) => a + b.prob * b.odds * amounts[i], 0),
  };
}
