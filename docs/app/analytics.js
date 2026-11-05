// あなたの収支分析(spec/11 2.5、W-21)。端末内の購入記録だけから計算する(外部に送信しない)。
// DOM を触らない純粋な関数。テスト: docs/app/tests/ledger.html
// この集計を賭け金の計算や「取り返す」提案に使わない(spec/08 3章)。

import { wilson } from './ledger.js';

export const FEW = 200; // これ未満は「参考」(spec/11 1.3)
export const MIN_SHOW = 30; // これ未満は率を出さず「件数不足」
export const BOOT_REPS = 200;

/** 決まった種から作る乱数(同じ入力なら同じ区間になるように) */
function mulberry32(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** 回収率の95%区間(レース単位のブートストラップ。races = [{stake, ret}]) */
export function bootstrapRoi(races, reps = BOOT_REPS, seed = 12345) {
  const n = races.length;
  if (!n) return null;
  const rnd = mulberry32(seed + n);
  const vals = [];
  for (let r = 0; r < reps; r++) {
    let s = 0; let p = 0;
    for (let i = 0; i < n; i++) { const x = races[Math.floor(rnd() * n)]; s += x.stake; p += x.ret; }
    vals.push(s ? p / s : 0);
  }
  vals.sort((a, b) => a - b);
  return [vals[Math.floor(reps * 0.025)], vals[Math.ceil(reps * 0.975) - 1]];
}

/**
 * 切り口ごとの集計。rows は ledger.summarize の rows(確定分だけ使う)、keyOf(row) は区分の名前。
 * 件数はレース数。戻り値: [{ key, races, bets, stake, ret, roi, ci, hitRate, hitCi, level: 'ok'|'few'|'insufficient' }]
 */
export function groupStats(rows, keyOf, { reps = BOOT_REPS } = {}) {
  const groups = new Map();
  rows.forEach((row) => {
    if (row.status === 'pending') return;
    const key = keyOf(row);
    if (!groups.has(key)) groups.set(key, new Map());
    const g = groups.get(key);
    const id = row.bet.race_id;
    if (!g.has(id)) g.set(id, { stake: 0, ret: 0, hit: false, bets: 0 });
    const r = g.get(id);
    r.stake += row.bet.amount;
    r.ret += row.payout;
    r.bets += 1;
    if (row.status === 'hit') r.hit = true;
  });
  return [...groups.entries()].map(([key, g]) => {
    const races = [...g.values()];
    const stake = races.reduce((a, b) => a + b.stake, 0);
    const ret = races.reduce((a, b) => a + b.ret, 0);
    const hits = races.filter((x) => x.hit).length;
    const n = races.length;
    const level = n < MIN_SHOW ? 'insufficient' : n < FEW ? 'few' : 'ok';
    return {
      key, races: n, bets: races.reduce((a, b) => a + b.bets, 0), stake, ret,
      roi: stake ? ret / stake : null,
      ci: level === 'insufficient' ? null : bootstrapRoi(races, reps),
      hitRate: n ? hits / n : null, hits,
      hitCi: level === 'insufficient' ? null : wilson(hits, n),
      level,
    };
  }).sort((a, b) => b.races - a.races || String(a.key).localeCompare(String(b.key)));
}

/** 締切時刻(JST の時)から時間帯 */
export function timeBand(hour) {
  if (hour === null || hour === undefined || Number.isNaN(hour)) return '不明';
  if (hour < 12) return '〜12時';
  if (hour < 17) return '12〜17時';
  return '17時〜';
}

/** 購入記録の期待値ランク: 項目が無い古い記録は「不明」、記録時にランクが無かったものは「なし」 */
export function evRankKey(bet) {
  if (!('ev_rank' in bet)) return '不明';
  return bet.ev_rank ? `ランク${bet.ev_rank}` : 'なし';
}

const ALLOC_NAMES = { equal: '均等', top: '上位厚め', prob: '確率に比例', payout: '払戻をそろえる', ev: '期待値に比例', manual: '手で入力' };
export function allocationKey(bet) {
  if (!('allocation' in bet) || !bet.allocation) return '不明';
  return ALLOC_NAMES[bet.allocation] || '不明';
}
