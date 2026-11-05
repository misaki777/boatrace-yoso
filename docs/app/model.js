// days/YYYYMMDD.json の読み替えと、フロントで計算する値(design/screens README 10章)
import { parseIso, nowMs, compactToDate } from './time.js';
import { parseCombination } from './format.js';

/**
 * レースの状態。締切前後は closed_at と現在時刻で判定し、JSON の state は canceled と no_prediction だけ見る。
 * 戻り値: canceled / result / open / wait
 */
export function raceStatus(race) {
  if (race.state === 'canceled') return 'canceled';
  if (race.result) return 'result';
  const close = parseIso(race.closed_at);
  if (!Number.isNaN(close) && close > nowMs()) return 'open';
  return 'wait';
}

/** 本命(p_rank=1)の艇 */
export function favoriteBoat(prediction) {
  if (!prediction || !Array.isArray(prediction.boats)) return null;
  return prediction.boats.find((b) => b.p_rank === 1) || null;
}

export function recommendedOf(race) {
  return (race.prediction && Array.isArray(race.prediction.recommended)) ? race.prediction.recommended : [];
}

/** 推奨の成績の種類: hit / refund / miss / skip / none(予想なし・中止など) */
export function outcomeKind(race) {
  const result = race.result;
  if (!result || !race.prediction) return 'none';
  const outcome = result.recommended_outcome;
  if (!outcome) return 'none';
  if (!outcome.bets) return 'skip';
  if (outcome.hits > 0) return 'hit';
  if (outcome.return > 0) return 'refund';
  return 'miss';
}

/** 払戻の一覧で、この組合せが的中しているか */
export function payoutFor(result, betType, combination) {
  if (!result || !result.payouts) return null;
  const list = result.payouts[betType] || [];
  return list.find((p) => p.combination === combination) || null;
}

/** 不成立・特払(combination が null でラベル付き)の払戻 */
export function specialPayout(result, betType) {
  if (!result || !result.payouts) return null;
  const list = result.payouts[betType] || [];
  return list.find((p) => p.combination === null && p.label) || null;
}

/** 組合せに欠場艇が含まれるか */
export function includesAbsent(combination, absentEntries) {
  if (!absentEntries || !absentEntries.length) return false;
  return parseCombination(combination).entries.some((e) => absentEntries.includes(e));
}

/** 推奨1行ぶんの結果: hit / refund / special / miss / null(結果なし) */
export function betOutcome(race, bet) {
  const result = race.result;
  if (!result) return null;
  const pay = payoutFor(result, bet.bet_type, bet.combination);
  if (pay) return { kind: 'hit', amount: pay.amount };
  if (includesAbsent(bet.combination, result.absent_entries)) return { kind: 'refund' };
  const special = specialPayout(result, bet.bet_type);
  if (special) return { kind: 'special', label: special.label, amount: special.amount };
  return { kind: 'miss' };
}

/** 「どれか的中」の確率(同じ券種の組合せは排他なので合計してよい) */
export function anyHitProb(recommended) {
  return recommended.reduce((sum, b) => sum + (b.prob || 0), 0);
}

/** race_id 'YYYYMMDD-SS-RR' を分解する */
export function parseRaceId(raceId) {
  const m = /^(\d{8})-(\d{2})-(\d{2})$/.exec(raceId || '');
  if (!m) return null;
  return { date: compactToDate(m[1]), compact: m[1], stadiumNumber: Number(m[2]), raceNumber: Number(m[3]) };
}

/** 1日分の中からレースを探す: { stadium, race, index } */
export function findRace(day, raceId) {
  for (const stadium of day.stadiums) {
    const index = stadium.races.findIndex((r) => r.race_id === raceId);
    if (index >= 0) return { stadium, race: stadium.races[index], index };
  }
  return null;
}

/** 1日分の全レース(場の情報を添える) */
export function allRaces(day) {
  const out = [];
  day.stadiums.forEach((stadium) => stadium.races.forEach((race) => out.push({ stadium, race })));
  return out;
}

/** 場ごとの的中数と回収率(中止・見送りは含めない。S1 場ごとの見出し) */
export function stadiumStats(stadium) {
  let stake = 0;
  let ret = 0;
  let hits = 0;
  stadium.races.forEach((race) => {
    if (race.state === 'canceled' || !race.result || !race.prediction) return;
    const o = race.result.recommended_outcome;
    if (!o || !o.bets) return;
    stake += o.stake || 0;
    ret += o.return || 0;
    if (o.hits > 0) hits += 1;
  });
  return { stake, ret, hits, roi: stake > 0 ? ret / stake : null };
}

/** レースの締切時刻(ミリ秒) */
export function closeMs(race) {
  return parseIso(race.closed_at);
}
