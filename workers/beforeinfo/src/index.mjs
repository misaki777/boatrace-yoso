// 直前情報の取得 Worker(spec/12)。Cron Trigger で5分ごとに起動する。
//
// 1. R2 の schedule/YYYYMMDD.json(朝ジョブが置く)を読む
// 2. 締切の15〜10分前のレースを1回だけ、公式の beforeinfo ページから取る(取り直しはしない)
//    (締切の早い順、1回の起動で最大3ページ、1.5秒以上の間隔で順番に)
// 3. 解析結果の JSON だけを R2 の beforeinfo/YYYYMMDD/SS-RR.json に保存(HTML は保存しない)
// 4. 保存したレースがあれば、GitHub の workflow_dispatch を1回だけ送る(前回から4分未満なら次の起動に回す)
//
// env: DATA(R2 binding)、TICK_PAGE_LIMIT(1回の起動の合計ページ数。既定5)、ODDS_*(src/odds.mjs)、WINDOW_START_MIN / WINDOW_END_MIN / LAST_CHANCE_MIN(取得の時間帯。既定15/10/5)、GH_TOKEN(secret)、DAILY_LIMIT(既定300。超えたらその日は止める)、GITHUB_REPO、GITHUB_WORKFLOW、DISPATCH_MODE
// ログには件数・状態・race_id・所要時間だけを出す。トークン・展示タイムなどの値・HTML は出さない。

import {
  MAX_CONSECUTIVE_FAILURES,
  MAX_PAGES_PER_TICK,
  UPSTREAM_TIMEOUT_MS,
  USER_AGENT,
  shouldFetch,
  parseWindow,
  parseTickPageLimit,
  emptyState,
  formatJstIso,
  jstDateString,
  minutesBefore,
  officialUrl,
  parseClosedAt,
  parseDailyLimit,
  resultKey,
  scheduleKey,
  shouldDispatch,
  stateKey,
  stoppedKey,
  waitBeforeNext,
} from "./core.mjs";
import { parseBeforeinfo } from "./parse.mjs";
import { oddsStateKey, oddsStoppedKey, parseOddsDailyLimit, runOdds } from "./odds.mjs";

export const DEFAULT_REPO = "misaki777/boatrace-yoso";
export const DEFAULT_WORKFLOW = "daily.yml";
export const DEFAULT_DISPATCH_MODE = "lock"; // daily.yml の直前予想の確定モード(CI 担当)

function defaultDeps() {
  return {
    fetchFn: (url, init) => fetch(url, init),
    sleep: (ms) => new Promise((r) => setTimeout(r, ms)),
    now: () => Date.now(),
    log: (obj) => console.log(JSON.stringify(obj)),
  };
}

export default {
  async scheduled(event, env, ctx) {
    ctx.waitUntil(runTick(env, defaultDeps()));
  },
  // 動作確認用: 状態の要約だけを返す(取得はしない。値もトークンも返さない)
  async fetch(request, env) {
    const date = jstDateString(Date.now());
    const state = (await readJson(env.DATA, stateKey(date))) ?? emptyState();
    const stopped = await readJson(env.DATA, stoppedKey(date));
    const body = {
      ok: true,
      date,
      has_token: Boolean(env.GH_TOKEN),
      has_schedule: (await env.DATA.head(scheduleKey(date))) !== null,
      used_today: state.count,
      limit_per_day: parseDailyLimit(env.DAILY_LIMIT),
      races_recorded: Object.keys(state.races).length,
      stopped: stopped ? stopped.reason : null,
      odds_used_today: ((await readJson(env.DATA, oddsStateKey(date))) ?? { count: 0 }).count,
      odds_limit_per_day: parseOddsDailyLimit(env.ODDS_DAILY_LIMIT),
      odds_stopped: ((await readJson(env.DATA, oddsStoppedKey(date))) ?? { reason: null }).reason,
    };
    return new Response(JSON.stringify(body), { headers: { "content-type": "application/json; charset=utf-8" } });
  },
};

async function readJson(bucket, key) {
  const obj = await bucket.get(key);
  if (!obj) return null;
  try {
    return JSON.parse(await obj.text());
  } catch (_e) {
    return null;
  }
}

async function writeJson(bucket, key, value) {
  await bucket.put(key, JSON.stringify(value), { httpMetadata: { contentType: "application/json" } });
}

/**
 * 1回の起動の処理。直前情報を先に(最大3ページ)、残りのページ数でオッズの記録(src/odds.mjs)を取る。
 * 2つは停止・上限を別々に持つ(片方が止まっても、もう片方は続ける)。ページの間隔(1.5秒)は共通。
 * 戻り値は要約(テスト用。ログにも同じものを出す)。
 */
export async function runTick(env, deps) {
  const startMs = deps.now();
  const date = jstDateString(startMs);
  const shared = { lastEnd: null, fetched: 0, tickLimit: parseTickPageLimit(env.TICK_PAGE_LIMIT) };
  const schedule = await readJson(env.DATA, scheduleKey(date));
  if (!schedule || !Array.isArray(schedule.races)) {
    const s = { event: "tick", date, result: "no_schedule", elapsed_ms: deps.now() - startMs };
    deps.log(s);
    return { ...s, odds: { result: "no_schedule", fetched: 0 } };
  }
  const summary = await runBeforeinfo(env, deps, { date, startMs, schedule, shared });
  summary.odds = await runOdds(env, deps, { date, schedule, shared, fetchPage, readJson, writeJson });
  return summary;
}

/** 直前情報の部分。 */
async function runBeforeinfo(env, deps, { date, startMs, schedule, shared }) {
  const bucket = env.DATA;
  const summary = { event: "tick", date, fetched: 0, saved: [], statuses: {}, dispatched: false };
  const finish = (extra = {}) => {
    Object.assign(summary, extra, { elapsed_ms: deps.now() - startMs });
    deps.log(summary);
    return summary;
  };

  if (await readJson(bucket, stoppedKey(date))) return finish({ result: "stopped" });

  const state = { ...emptyState(), ...((await readJson(bucket, stateKey(date))) ?? {}) };
  const limit = parseDailyLimit(env.DAILY_LIMIT);
  const win = parseWindow(env);

  // 今取る候補(締切の早い順)
  const candidates = [];
  for (const race of schedule.races) {
    const closedMs = parseClosedAt(race.closed_at);
    if (closedMs === null || !race.race_id) continue;
    if (shouldFetch(minutesBefore(closedMs, startMs), state.races[race.race_id], win)) {
      candidates.push({ race, closedMs, attempt: 1 });
    }
  }
  candidates.sort((a, b) => a.closedMs - b.closedMs || (a.race.race_id < b.race.race_id ? -1 : 1));
  summary.candidates = candidates.length;

  let stopReason = null;
  const biMax = Math.min(MAX_PAGES_PER_TICK, shared.tickLimit);
  for (const c of candidates) {
    if (summary.fetched >= biMax) break; // 残りは次の起動へ
    if (state.count >= limit) {
      stopReason = `1日の上限(${limit}ページ)に達しました`;
      break;
    }
    const wait = waitBeforeNext(shared.lastEnd, deps.now());
    if (wait > 0) await deps.sleep(wait);
    // 待っている間に4分前を過ぎたら取らない
    const minutes = minutesBefore(c.closedMs, deps.now());
    if (minutes < win.last) continue;

    const { race } = c;
    const t0 = deps.now();
    const res = await fetchPage(deps, officialUrl(race.jcd, race.rno, date));
    const lastEnd = deps.now();
    shared.lastEnd = lastEnd;
    shared.fetched += 1;
    state.count += 1;
    summary.fetched += 1;

    if (res.status === 403 || res.status === 429) {
      stopReason = `公式サイトが HTTP ${res.status} を返しました`;
      summary.statuses[race.race_id] = `http_${res.status}`;
      break;
    }
    let parsed = null;
    if (res.status === 200 && res.text !== null) {
      try {
        parsed = parseBeforeinfo(res.text);
      } catch (_e) {
        parsed = null;
      }
    }
    if (!parsed) {
      state.consecutive_failures += 1;
      state.races[race.race_id] = { attempt: c.attempt, status: "failed" };
      summary.statuses[race.race_id] = res.status === 200 ? "parse_error" : `http_${res.status ?? "timeout"}`;
      deps.log({ event: "page", race_id: race.race_id, status: summary.statuses[race.race_id], minutes_before_close: Math.round(minutesBefore(c.closedMs, lastEnd) * 10) / 10, ms: lastEnd - t0 });
      if (state.consecutive_failures >= MAX_CONSECUTIVE_FAILURES) {
        stopReason = `${MAX_CONSECUTIVE_FAILURES}回続けて失敗しました`;
        break;
      }
      continue;
    }
    state.consecutive_failures = 0;
    const doc = {
      race_id: race.race_id,
      fetched_at: formatJstIso(lastEnd),
      minutes_before_close: Math.round(minutesBefore(c.closedMs, lastEnd) * 10) / 10,
      attempt: c.attempt,
      status: parsed.status,
      weather: parsed.weather,
      boats: parsed.boats,
    };
    await writeJson(bucket, resultKey(date, race.jcd, race.rno), doc);
    state.races[race.race_id] = { attempt: c.attempt, status: parsed.status };
    summary.statuses[race.race_id] = parsed.status;
    summary.saved.push(race.race_id);
    if (!state.pending_dispatch.includes(race.race_id)) state.pending_dispatch.push(race.race_id);
    deps.log({ event: "page", race_id: race.race_id, status: parsed.status, minutes_before_close: doc.minutes_before_close, ms: lastEnd - t0 });
  }

  if (stopReason) {
    await writeJson(bucket, stoppedKey(date), { date, stopped_at: formatJstIso(deps.now()), reason: stopReason });
    summary.stopped_reason = stopReason;
  }

  // dispatch(1回の起動で最大1回。4分以内の再送はしない)
  if (shouldDispatch(state.pending_dispatch, state.last_dispatch_ms, deps.now())) {
    const ok = await dispatch(env, deps, state.pending_dispatch);
    if (ok) {
      summary.dispatched = true;
      summary.dispatched_races = state.pending_dispatch.length;
      state.last_dispatch_ms = deps.now();
      state.pending_dispatch = [];
    }
  }

  if (summary.fetched > 0 || summary.dispatched) await writeJson(bucket, stateKey(date), state);
  return finish({ result: stopReason ? "stopped_now" : "ok", used_today: state.count, limit_per_day: limit });
}

export async function fetchPage(deps, url) {
  const controller = typeof AbortController === "function" ? new AbortController() : null;
  let timer = null;
  const timeout = new Promise((resolve) => {
    timer = setTimeout(() => {
      if (controller) controller.abort();
      resolve({ status: null, text: null });
    }, deps.upstreamTimeoutMs ?? UPSTREAM_TIMEOUT_MS);
  });
  const request = (async () => {
    const res = await deps.fetchFn(url, {
      headers: { "User-Agent": USER_AGENT, Accept: "text/html" },
      redirect: "manual",
      signal: controller ? controller.signal : undefined,
    });
    if (res.status !== 200) return { status: res.status, text: null };
    return { status: 200, text: await res.text() };
  })().catch(() => ({ status: null, text: null }));
  try {
    return await Promise.race([request, timeout]);
  } finally {
    clearTimeout(timer);
  }
}

/** GitHub の workflow_dispatch を送る(トークンはログに出さない)。成功なら true。 */
export async function dispatch(env, deps, raceIds) {
  if (!env.GH_TOKEN) {
    deps.log({ event: "dispatch", ok: false, error: "GH_TOKEN が設定されていません" });
    return false;
  }
  const repo = env.GITHUB_REPO || DEFAULT_REPO;
  const workflow = env.GITHUB_WORKFLOW || DEFAULT_WORKFLOW;
  const body = {
    ref: "main",
    inputs: { mode: env.DISPATCH_MODE || DEFAULT_DISPATCH_MODE, race_ids: raceIds.join(","), date: "", deploy: true },
  };
  try {
    const res = await deps.fetchFn(`https://api.github.com/repos/${repo}/actions/workflows/${workflow}/dispatches`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${env.GH_TOKEN}`,
        Accept: "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "br-yoso-beforeinfo",
        "Content-Type": "application/json",
      },
      body: JSON.stringify(body),
    });
    const ok = res.status === 204;
    deps.log({ event: "dispatch", ok, status: res.status, races: raceIds.length });
    return ok;
  } catch (_e) {
    deps.log({ event: "dispatch", ok: false, status: null, races: raceIds.length });
    return false;
  }
}
