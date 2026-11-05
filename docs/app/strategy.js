// 推奨買い目の計算し直し(spec/07 4章・9.6)。DOM を触らない純粋な関数だけを置く。
// 規則と答えは docs/data/sample/strategy_cases.json(エクスポート担当の Python と共通)。
// テスト: docs/app/tests/strategy.html(本番の画面からはリンクしない)

/** 定数(strategy_cases.json の constants と同じ。テストページで一致を確かめる) */
export const CONSTANTS = Object.freeze({
  bet_type_order: ['win', 'exacta', 'trifecta'],
  k_ranges: { win: [1, 3], exacta: [1, 10], trifecta: [1, 20] },
  min_prob_choices: [null, 0.01, 0.02, 0.03, 0.05],
  skip_confidence_choices: ['none', 'ara', 'chu_or_below'],
  ev_filter_choices: ['none', 'B_or_above', 'A_only'],
  ev_filter_ranks: { B_or_above: ['A', 'B'], A_only: ['A'] },
  no_odds_choices: ['prob_only', 'skip'],
  confidence_kata: 0.5,
  confidence_ara: 0.35,
  stake_range: [100, 1000, 100],
});

/** 既定の設定(strategy_cases.json の default_settings と同じ) */
export const DEFAULT_SETTINGS = Object.freeze({
  bet_types: Object.freeze({ trifecta: 5 }),
  min_prob: null,
  skip_confidence: 'none',
  ev_filter: 'none',
  no_odds: 'prob_only',
  stake: 100,
});

/** 期待値ランクの境界(spec/07 3.2。エクスポートの EV_RANK_BOUNDS と同じ) */
export const EV_RANK_BOUNDS = Object.freeze({ A: 1.2, B: 1.0, C: 0.8 });

/** 既定の設定の複製(書き換えてよいもの) */
export function defaultSettings() {
  return { ...DEFAULT_SETTINGS, bet_types: { ...DEFAULT_SETTINGS.bet_types } };
}

const isPlainObject = (v) => v !== null && typeof v === 'object' && !Array.isArray(v);
const isInt = (v) => typeof v === 'number' && Number.isInteger(v);

/** bet_types が正しければその複製、正しくなければ null */
function validBetTypes(value) {
  if (!isPlainObject(value)) return null;
  const keys = Object.keys(value);
  if (!keys.length) return null;
  const out = {};
  for (const key of keys) {
    const range = CONSTANTS.k_ranges[key];
    const k = value[key];
    if (!range || !isInt(k) || k < range[0] || k > range[1]) return null;
    out[key] = k;
  }
  return out;
}

function validStake(v) {
  const [lo, hi, step] = CONSTANTS.stake_range;
  return isInt(v) && v >= lo && v <= hi && (v - lo) % step === 0;
}

/**
 * 設定を正しい形にする。オブジェクトでなければ全部を既定に、項目ごとに不正なら、その項目だけ既定に戻す。
 * 戻り値: { settings, replaced }(replaced = 既定に戻した項目があったか)
 */
export function normalizeSettingsDetail(input) {
  const out = defaultSettings();
  if (!isPlainObject(input)) return { settings: out, replaced: input !== undefined };
  let replaced = false;
  const take = (key, ok) => {
    if (!(key in input)) return;
    if (ok(input[key])) out[key] = input[key];
    else replaced = true;
  };
  if ('bet_types' in input) {
    const bt = validBetTypes(input.bet_types);
    if (bt) out.bet_types = bt;
    else replaced = true;
  }
  take('min_prob', (v) => CONSTANTS.min_prob_choices.includes(v));
  take('skip_confidence', (v) => CONSTANTS.skip_confidence_choices.includes(v));
  take('ev_filter', (v) => CONSTANTS.ev_filter_choices.includes(v));
  take('no_odds', (v) => CONSTANTS.no_odds_choices.includes(v));
  take('stake', validStake);
  return { settings: out, replaced };
}

export function normalizeSettings(input) {
  return normalizeSettingsDetail(input).settings;
}

/** 2つの設定が同じか */
export function sameSettings(a, b) {
  const x = normalizeSettings(a);
  const y = normalizeSettings(b);
  const btKeys = new Set([...Object.keys(x.bet_types), ...Object.keys(y.bet_types)]);
  for (const k of btKeys) if (x.bet_types[k] !== y.bet_types[k]) return false;
  return ['min_prob', 'skip_confidence', 'ev_filter', 'no_odds', 'stake'].every((k) => x[k] === y[k]);
}

/** 選び方に関わる項目(stake 以外)が既定と同じか */
export function isDefaultSelection(settings) {
  return sameSettings({ ...settings, stake: 100 }, { ...DEFAULT_SETTINGS, stake: 100 });
}

/** 期待値 → ランク(A: ≥1.20、B: ≥1.00、C: ≥0.80、D: それ未満)。null なら null */
export function evRankOf(ev) {
  if (typeof ev !== 'number' || Number.isNaN(ev)) return null;
  if (ev >= EV_RANK_BOUNDS.A) return 'A';
  if (ev >= EV_RANK_BOUNDS.B) return 'B';
  if (ev >= EV_RANK_BOUNDS.C) return 'C';
  return 'D';
}

/** オッズの値を数にする(下限・上限の形は下限)。数でなければ null。丸め直さない */
function oddsNumber(v) {
  if (typeof v === 'number') return Number.isFinite(v) && v > 0 ? v : null;
  if (isPlainObject(v) && typeof v.lower_limit === 'number') return v.lower_limit > 0 ? v.lower_limit : null;
  return null;
}

/**
 * Worker の応答の odds(spec/07 2.6 の入れ子)から、券種・組合せのオッズを引く。
 * 戻り値: { has: その券種が odds にあるか, odds: 数 | null }
 */
export function workerOddsFor(workerOdds, betType, combination) {
  if (!isPlainObject(workerOdds) || !(betType in workerOdds) || !isPlainObject(workerOdds[betType])) return { has: false, odds: null };
  const table = workerOdds[betType];
  const text = String(combination);
  const ordered = text.includes('=');
  let parts = text.split(ordered ? '=' : '-');
  if (ordered) parts = parts.map(Number).sort((a, b) => a - b).map(String);
  let node = table;
  for (const p of parts) {
    if (!isPlainObject(node) || !(p in node)) {
      // 単勝・複勝は1段(node[p] が数か {lower_limit})なので、最後の段で抜ける
      return { has: true, odds: null };
    }
    node = node[p];
    if (!isPlainObject(node) || 'lower_limit' in node) break;
  }
  return { has: true, odds: oddsNumber(node) };
}

/** 手入力のオッズ(manual[券種][組合せ])。無ければ null */
function manualOddsFor(manual, betType, combination) {
  if (!isPlainObject(manual) || !isPlainObject(manual[betType])) return null;
  return oddsNumber(manual[betType][combination]);
}

/**
 * 1つの組合せの評価: 出どころ(manual / worker / public / none)、オッズ、期待値、ランク。
 * 優先順位: 手入力 → Worker(その券種が odds にあれば) → 公開JSONの ev_rank → なし。
 * pick.ev_rank のキーが無いのと null は、どちらも「オッズなし」(選び方では同じに扱う。出どころの表示だけ public / none に分ける)。
 */
export function evaluatePick(pick, betType, oddsInput = {}) {
  const prob = pick.prob;
  const manual = manualOddsFor(oddsInput.manual, betType, pick.combination);
  if (manual !== null) {
    const ev = prob * manual;
    return { combination: pick.combination, prob, source: 'manual', odds: manual, ev, ev_rank: evRankOf(ev) };
  }
  const worker = oddsInput.worker && oddsInput.worker.odds !== undefined ? workerOddsFor(oddsInput.worker.odds, betType, pick.combination) : { has: false };
  if (worker.has) {
    const ev = worker.odds === null ? null : prob * worker.odds;
    return { combination: pick.combination, prob, source: 'worker', odds: worker.odds, ev, ev_rank: evRankOf(ev) };
  }
  if ('ev_rank' in pick) {
    // 公開JSON(ローカルJSONなら odds・ev の数値もある)。ev_rank が null でも出どころは public(ランクは「なし」)
    const odds = typeof pick.odds === 'number' ? pick.odds : null;
    const ev = typeof pick.ev === 'number' ? pick.ev : null;
    return { combination: pick.combination, prob, source: 'public', odds, ev, ev_rank: pick.ev_rank ?? null };
  }
  return { combination: pick.combination, prob, source: 'none', odds: null, ev: null, ev_rank: null };
}

/** 券種の picks をすべて評価する(picks の順序は変えない) */
export function evaluatePicks(picks, betType, oddsInput = {}) {
  const list = (picks && Array.isArray(picks[betType])) ? picks[betType] : [];
  return list.map((p) => ({ ...evaluatePick(p, betType, oddsInput), required_odds: p.required_odds }));
}

/** 堅さの見送りに当たるか */
export function skippedByConfidence(confidence, skipConfidence) {
  if (typeof confidence !== 'number') return false;
  if (skipConfidence === 'ara') return confidence < CONSTANTS.confidence_ara;
  if (skipConfidence === 'chu_or_below') return confidence < CONSTANTS.confidence_kata;
  return false;
}

/**
 * 推奨買い目を計算する。race = { confidence, picks }。
 * 戻り値: { bets: [{bet_type, combination, prob, required_odds, source, odds, ev, ev_rank}], skipped: 'confidence' | null }
 */
export function computeRecommendation(race, settingsInput, oddsInput = {}) {
  const settings = normalizeSettings(settingsInput);
  const bets = [];
  const allowed = settings.ev_filter === 'none' ? null : CONSTANTS.ev_filter_ranks[settings.ev_filter];
  for (const betType of CONSTANTS.bet_type_order) {
    const k = settings.bet_types[betType];
    if (!k) continue;
    const evaluated = evaluatePicks(race.picks, betType, oddsInput);
    const kept = evaluated.filter((e) => {
      if (settings.min_prob !== null && e.prob < settings.min_prob) return false;
      if (allowed) {
        if (e.ev_rank === null) return settings.no_odds === 'prob_only';
        return allowed.includes(e.ev_rank);
      }
      return true;
    });
    kept.slice(0, k).forEach((e) => bets.push({ bet_type: betType, ...e }));
  }
  if (skippedByConfidence(race.confidence, settings.skip_confidence)) return { bets: [], skipped: 'confidence' };
  return { bets, skipped: null };
}

/** 推奨の組合せだけ({bet_type, combination} の配列) */
export function recommend(race, settings, oddsInput = {}) {
  return computeRecommendation(race, settings, oddsInput).bets.map((b) => ({ bet_type: b.bet_type, combination: b.combination }));
}
