// 締切前オッズの記録(spec/13、利用者承認 2026-11-07)。直前情報と同じ Cron の起動の中で、直前情報の後に動く。
//
// 全レース(R2 の schedule/YYYYMMDD.json)について、締切前の窓(既定 8〜3分前。ODDS_WINDOW_*)に
// 単勝・複勝(tf)と2連単・2連複(2tf)の各ページを1回ずつ取り、解析結果を R2 の odds/YYYYMMDD/SS-RR.json に保存する。
// 3連単(3t)は ODDS_TYPES に足せば取る(既定は取らない)。HTML は保存しない。
// 解析は中継 Worker と同じ workers/odds-proxy/src/parse.mjs を使う(共通 fixture で Python と一致を確認済み)。
//
// 上限・停止は直前情報とは別: 1日 ODDS_DAILY_LIMIT(既定300)ページ、公式の 403/429 か3回続けての失敗で
// odds/YYYYMMDD/_stopped.json を書き、その日のオッズの取得だけを止める。
// ログには race_id・券種・状態・締切までの分・所要時間だけを出す(オッズの数値は出さない)。

import { isFinalLabel, parsePage } from "../../odds-proxy/src/parse.mjs";
import { formatJstIso, minutesBefore, parseClosedAt, waitBeforeNext } from "./core.mjs";

export const ODDS_TYPE_ORDER = ["tf", "2tf", "3t"]; // 取る順番
export const ODDS_PAGE = { tf: "oddstf", "2tf": "odds2tf", "3t": "odds3t" };
export const DEFAULT_ODDS_TYPES = ["tf", "2tf"];
export const DEFAULT_ODDS_WINDOW = { start: 8, end: 3 }; // 締切の8〜3分前(Cron が5分ごとなので幅5分)
export const MIN_ODDS_END_MIN = 2; // これより締切に近い時刻には取らない
export const DEFAULT_ODDS_DAILY_LIMIT = 300;
export const MAX_ODDS_CONSECUTIVE_FAILURES = 3;

const pad = (n) => String(n).padStart(2, "0");
export const oddsStateKey = (date) => `odds/${date}/_state.json`;
export const oddsStoppedKey = (date) => `odds/${date}/_stopped.json`;
export const oddsResultKey = (date, jcd, rno) => `odds/${date}/${pad(Number(jcd))}-${pad(Number(rno))}.json`;

export function oddsUrl(type, jcd, rno, date) {
  return `https://www.boatrace.jp/owpc/pc/race/${ODDS_PAGE[type]}?rno=${Number(rno)}&jcd=${pad(Number(jcd))}&hd=${date}`;
}

/** ODDS_TYPES("tf,2tf,3t" など)→ 取る順に並べた券種。不正・空なら既定(tf, 2tf)。 */
export function parseOddsTypes(text) {
  if (text === undefined || text === null || String(text).trim() === "") return DEFAULT_ODDS_TYPES.slice();
  const items = String(text).split(",").map((s) => s.trim());
  if (items.some((t) => !ODDS_TYPE_ORDER.includes(t))) return DEFAULT_ODDS_TYPES.slice();
  return ODDS_TYPE_ORDER.filter((t) => items.includes(t));
}

export function parseOddsDailyLimit(text) {
  const s = String(text ?? "").trim();
  return /^\d+$/.test(s) && Number(s) > 0 ? Number(s) : DEFAULT_ODDS_DAILY_LIMIT;
}

/** ODDS_WINDOW_START_MIN / ODDS_WINDOW_END_MIN → {start, end}。不正なら既定。 */
export function parseOddsWindow(env = {}) {
  const num = (v) => (v === undefined || v === null || String(v).trim() === "" ? NaN : Number(v));
  const start = num(env.ODDS_WINDOW_START_MIN);
  const end = num(env.ODDS_WINDOW_END_MIN);
  const s = Number.isFinite(start) ? start : DEFAULT_ODDS_WINDOW.start;
  const e = Number.isFinite(end) ? end : DEFAULT_ODDS_WINDOW.end;
  if (!(s > e && e >= MIN_ODDS_END_MIN && s <= 60)) return { ...DEFAULT_ODDS_WINDOW };
  return { start: s, end: e };
}

/** このレースでまだ取っていない券種(窓の外なら空)。 */
export function pendingOddsTypes(minutes, record, types, window) {
  if (minutes < window.end || minutes > window.start) return [];
  const done = (record && record.pages) || {};
  return types.filter((t) => !(t in done));
}

export function emptyOddsState() {
  return { count: 0, consecutive_failures: 0, races: {} };
}

/**
 * オッズの記録の部分。ctx: {date, schedule, shared: {lastEnd, fetched, tickLimit}, fetchPage, readJson, writeJson}
 * 戻り値は要約(ログにも出す)。
 */
export async function runOdds(env, deps, ctx) {
  const { date, schedule, shared, fetchPage, readJson, writeJson } = ctx;
  const bucket = env.DATA;
  const startMs = deps.now();
  const summary = { event: "odds_tick", date, fetched: 0, saved: [], statuses: {} };
  const finish = (extra = {}) => {
    Object.assign(summary, extra, { elapsed_ms: deps.now() - startMs });
    deps.log(summary);
    return summary;
  };
  if (await readJson(bucket, oddsStoppedKey(date))) return finish({ result: "stopped" });

  const state = { ...emptyOddsState(), ...((await readJson(bucket, oddsStateKey(date))) ?? {}) };
  const limit = parseOddsDailyLimit(env.ODDS_DAILY_LIMIT);
  const types = parseOddsTypes(env.ODDS_TYPES);
  const win = parseOddsWindow(env);

  // 候補: 窓の中で未取得の券種があるレース。締切が近い順
  const candidates = [];
  for (const race of schedule.races) {
    const closedMs = parseClosedAt(race.closed_at);
    if (closedMs === null || !race.race_id) continue;
    const pending = pendingOddsTypes(minutesBefore(closedMs, deps.now()), state.races[race.race_id], types, win);
    if (pending.length > 0) candidates.push({ race, closedMs, pending });
  }
  candidates.sort((a, b) => a.closedMs - b.closedMs || (a.race.race_id < b.race.race_id ? -1 : 1));
  summary.candidates = candidates.length;

  let stopReason = null;
  outer: for (const c of candidates) {
    const { race } = c;
    const record = state.races[race.race_id] ?? { pages: {} };
    let doc = null; // このレースの保存内容(取れたページがあれば書く)
    for (const type of c.pending) {
      if (shared.fetched >= shared.tickLimit) break outer; // 1回の起動の合計ページ数。残りは次の起動へ
      if (state.count >= limit) {
        stopReason = `1日の上限(${limit}ページ)に達しました`;
        break outer;
      }
      const wait = waitBeforeNext(shared.lastEnd, deps.now());
      if (wait > 0) await deps.sleep(wait);
      if (minutesBefore(c.closedMs, deps.now()) < win.end) break; // 待つ間に窓を過ぎた
      const t0 = deps.now();
      const res = await fetchPage(deps, oddsUrl(type, race.jcd, race.rno, date));
      const end = deps.now();
      shared.lastEnd = end;
      shared.fetched += 1;
      state.count += 1;
      summary.fetched += 1;
      const minutes = Math.round(minutesBefore(c.closedMs, end) * 10) / 10;
      const key = `${race.race_id}:${type}`;

      if (res.status === 403 || res.status === 429) {
        record.pages[type] = `http_${res.status}`;
        state.races[race.race_id] = record;
        summary.statuses[key] = `http_${res.status}`;
        stopReason = `公式サイトが HTTP ${res.status} を返しました`;
        deps.log({ event: "odds_page", race_id: race.race_id, type, status: `http_${res.status}`, minutes_before_close: minutes, ms: end - t0 });
        if (doc) await saveDoc();
        break outer;
      }
      let parsed = null;
      let status;
      if (res.status === 200 && res.text !== null) {
        try {
          parsed = parsePage(type, res.text);
          status = "ok";
        } catch (_e) {
          status = "parse_error";
        }
      } else {
        status = "error";
      }
      record.pages[type] = status;
      state.races[race.race_id] = record;
      summary.statuses[key] = status;
      deps.log({ event: "odds_page", race_id: race.race_id, type, status, minutes_before_close: minutes, ms: end - t0 });
      if (!parsed) {
        state.consecutive_failures += 1;
        if (state.consecutive_failures >= MAX_ODDS_CONSECUTIVE_FAILURES) {
          stopReason = `${MAX_ODDS_CONSECUTIVE_FAILURES}回続けて失敗しました`;
          if (doc) await saveDoc();
          break outer;
        }
        continue;
      }
      state.consecutive_failures = 0;
      if (!doc) {
        doc = (await readJson(bucket, oddsResultKey(date, race.jcd, race.rno))) ?? {
          race_id: race.race_id,
          closed_at: race.closed_at,
          pages: {},
          odds: {},
        };
      }
      doc.pages[type] = {
        status: "ok",
        fetched_at: formatJstIso(end),
        minutes_before_close: minutes,
        odds_time_label: parsed.label,
        is_final_odds: isFinalLabel(parsed.label),
      };
      Object.assign(doc.odds, parsed.odds);
    }
    if (doc) await saveDoc();

    async function saveDoc() {
      await writeJson(bucket, oddsResultKey(date, race.jcd, race.rno), doc);
      summary.saved.push(race.race_id);
      doc = null;
    }
  }

  if (stopReason) {
    await writeJson(bucket, oddsStoppedKey(date), { date, stopped_at: formatJstIso(deps.now()), reason: stopReason });
    summary.stopped_reason = stopReason;
  }
  if (summary.fetched > 0) await writeJson(bucket, oddsStateKey(date), state);
  return finish({ result: stopReason ? "stopped_now" : "ok", used_today: state.count, limit_per_day: limit });
}
