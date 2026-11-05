// 購入記録の収支の計算(DOM を触らない純粋な関数。テスト: docs/app/tests/ledger.html)
// 払戻は保存せず、公開JSONの結果(result.payouts)から毎回計算する(spec/08 5.4。結果が直っても追従する)。
import { parseCombination } from './format.js';

export const DEFAULT_BUDGET = Object.freeze({ key: 'settings', day_limit: null, month_limit: null, streak_warn: 5 });
const MARKS = ['◎', '○', '▲', '△'];

/**
 * 1件の払戻。race は days の race(読めていなければ null)。
 * 戻り値: { status: 'hit'|'miss'|'refund'|'special'|'pending', payout: 円 | null }
 */
export function settleBet(bet, race) {
  if (!race) return { status: 'pending', payout: null };
  if (race.state === 'canceled') return { status: 'refund', payout: bet.amount };
  const r = race.result;
  if (!r) return { status: 'pending', payout: null };
  const absent = r.absent_entries || [];
  if (parseCombination(bet.combination).entries.some((e) => absent.includes(e))) return { status: 'refund', payout: bet.amount };
  const list = (r.payouts && r.payouts[bet.bet_type]) || [];
  const hit = list.find((p) => p.combination === bet.combination);
  if (hit) return { status: 'hit', payout: Math.floor((bet.amount / 100) * hit.amount) };
  const special = list.find((p) => p.combination === null && p.label);
  if (special) return { status: 'special', payout: Math.floor((bet.amount / 100) * special.amount), label: special.label };
  if (!r.payouts || !(bet.bet_type in r.payouts)) return { status: 'pending', payout: null }; // 払戻の無い券種(公開JSONは単勝・2連単・3連単だけ)
  return { status: 'miss', payout: 0 };
}

/**
 * 収支のまとめ。bets は購入記録、raceOf(race_id) は race か null。
 * 並びは購入時刻の順。最大ドローダウン = 確定した分の累計収支の、それまでの最高値からの最大の下げ幅(円)。
 * 連敗はレース単位(そのレースの払戻 < 投資 を負けとする)。
 */
export function summarize(bets, raceOf) {
  const sorted = bets.slice().sort((a, b) => (a.bought_at < b.bought_at ? -1 : a.bought_at > b.bought_at ? 1 : 0));
  let stake = 0; let ret = 0; let pendingStake = 0; let hits = 0; let settledCount = 0;
  const rows = sorted.map((bet) => ({ bet, ...settleBet(bet, raceOf(bet.race_id)) }));
  const raceOrder = [];
  const byRace = new Map();
  let cum = 0; let peak = 0; let maxDd = 0;
  const series = [];
  rows.forEach((row) => {
    if (row.status === 'pending') { pendingStake += row.bet.amount; return; }
    stake += row.bet.amount;
    ret += row.payout;
    settledCount += 1;
    if (row.status === 'hit') hits += 1;
    cum += row.payout - row.bet.amount;
    peak = Math.max(peak, cum);
    maxDd = Math.max(maxDd, peak - cum);
    series.push({ at: row.bet.bought_at, cum });
    if (!byRace.has(row.bet.race_id)) { byRace.set(row.bet.race_id, { stake: 0, ret: 0 }); raceOrder.push(row.bet.race_id); }
    const g = byRace.get(row.bet.race_id);
    g.stake += row.bet.amount;
    g.ret += row.payout;
  });
  let streak = 0; let maxStreak = 0;
  raceOrder.forEach((id) => {
    const g = byRace.get(id);
    if (g.ret < g.stake) { streak += 1; maxStreak = Math.max(maxStreak, streak); } else streak = 0;
  });
  return {
    rows, stake, ret, profit: ret - stake, roi: stake ? ret / stake : null, pendingStake,
    count: bets.length, settledCount, hits, races: raceOrder.length,
    maxDrawdown: maxDd, currentStreak: streak, maxStreak, series,
  };
}

/** 月別(購入時刻の JST の月) */
export function byMonth(rows, monthOf) {
  const m = new Map();
  rows.forEach((row) => {
    const key = monthOf(row.bet.bought_at);
    if (!m.has(key)) m.set(key, { month: key, stake: 0, ret: 0, pending: 0, count: 0 });
    const g = m.get(key);
    g.count += 1;
    if (row.status === 'pending') g.pending += row.bet.amount;
    else { g.stake += row.bet.amount; g.ret += row.payout; }
  });
  return [...m.values()].sort((a, b) => (a.month < b.month ? 1 : -1));
}

/**
 * 予算の警告。spent は今日・今月の投資額(未確定も含む)。80% と 100% で警告(上限を超えても記録はできる)。
 * 戻り値: [{ kind: 'day'|'month'|'streak', level: 'near'|'over', ... }]
 */
export function budgetWarnings(budget, { daySpent, monthSpent, currentStreak }) {
  const out = [];
  const check = (kind, limit, spent) => {
    if (!limit) return;
    if (spent >= limit) out.push({ kind, level: 'over', limit, spent });
    else if (spent >= limit * 0.8) out.push({ kind, level: 'near', limit, spent });
  };
  check('day', budget.day_limit, daySpent);
  check('month', budget.month_limit, monthSpent);
  if (budget.streak_warn && currentStreak >= budget.streak_warn) out.push({ kind: 'streak', level: 'over', streak: currentStreak, limit: budget.streak_warn });
  return out;
}

/** 予算の値を正しい形にする */
export function normalizeBudget(v) {
  const out = { ...DEFAULT_BUDGET };
  if (!v || typeof v !== 'object') return out;
  const yen = (x) => (Number.isInteger(x) && x >= 100 && x <= 10000000 ? x : null);
  out.day_limit = yen(v.day_limit);
  out.month_limit = yen(v.month_limit);
  if (Number.isInteger(v.streak_warn) && v.streak_warn >= 0 && v.streak_warn <= 50) out.streak_warn = v.streak_warn;
  return out;
}

/** 印の正規化: { 枠番: 印 }。同じ印は1艇だけ */
export function normalizeMarks(marks) {
  const out = {};
  const used = new Set();
  Object.entries(marks || {}).forEach(([entry, m]) => {
    if (/^[1-6]$/.test(entry) && MARKS.includes(m) && !used.has(m)) { out[entry] = m; used.add(m); }
  });
  return out;
}

/**
 * 印の成績(S9)。items = [{ marks, race }](race は結果のあるもの)。
 * ◎の1着率・連対率、◎-○-▲ の3連単を100円買った場合の回収率(仮定)、モデルの本命(p_rank=1)の1着率(同じレース)。
 */
export function markStats(items) {
  let n = 0; let honWin = 0; let honTop2 = 0; let modelWin = 0; let tri = 0; let triStake = 0; let triRet = 0;
  items.forEach(({ marks, race }) => {
    const r = race && race.result;
    if (!r || race.state === 'canceled' || !Array.isArray(r.order)) return;
    const entryOf = (m) => Number(Object.keys(marks).find((k) => marks[k] === m));
    const hon = entryOf('◎');
    if (!hon) return;
    n += 1;
    if (r.order[0] === hon) honWin += 1;
    if (r.order[0] === hon || r.order[1] === hon) honTop2 += 1;
    const fav = race.prediction && race.prediction.boats.find((b) => b.p_rank === 1);
    if (fav && r.order[0] === fav.entry) modelWin += 1;
    const tai = entryOf('○'); const ana = entryOf('▲');
    if (tai && ana) {
      tri += 1;
      triStake += 100;
      const pay = ((r.payouts && r.payouts.trifecta) || []).find((p) => p.combination === `${hon}-${tai}-${ana}`);
      if (pay) triRet += pay.amount;
    }
  });
  return { races: n, honWin, honTop2, modelWin, triRaces: tri, triStake, triRet, triRoi: triStake ? triRet / triStake : null };
}

/** 比率の95%区間(Wilson)。件数0なら null */
export function wilson(k, n) {
  if (!n) return null;
  const z = 1.96; const p = k / n;
  const d = 1 + (z * z) / n;
  const c = p + (z * z) / (2 * n);
  const h = z * Math.sqrt((p * (1 - p)) / n + (z * z) / (4 * n * n));
  return [(c - h) / d, (c + h) / d];
}
