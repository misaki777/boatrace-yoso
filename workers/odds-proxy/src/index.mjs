// 締切前オッズの中継 Worker(spec/07 9章)。
//
// GET /odds?date=YYYYMMDD&jcd=02&rno=1&types=3t,2tf,tf&closed_at=...  (ヘッダ X-Odds-Key: 合言葉)
//
// 処理の順番: CORS(オリジン)→ プリフライト → パスとメソッド → 合言葉 → 入力チェック → 締切 →
//             停止フラグ → キャッシュ → 1日の上限 → 公式サイトから順番に取得(ページの間は1秒以上)
//
// 環境(env):
//   ODDS_KEY        合言葉(wrangler secret put ODDS_KEY)
//   ALLOWED_ORIGINS 許可するオリジン(カンマ区切り)
//   DAILY_LIMIT     1日に公式サイトから取るページ数の上限(既定300)
//   ODDS_KV         KV の binding(キャッシュ・使用数・停止フラグ)
//
// ログには race_id・券種・状態・キャッシュかどうか・所要時間・使用数だけを出す。
// オッズの数値・合言葉・HTML は出さない(spec/07 9.9)。

import {
  CACHE_TTL_SEC,
  COUNT_TTL_SEC,
  STOPPED_TTL_SEC,
  UPSTREAM_TIMEOUT_MS,
  USER_AGENT,
  cacheKey,
  countKey,
  formatJstIso,
  isCacheFresh,
  isClosed,
  isOriginAllowed,
  officialUrl,
  parseAllowedOrigins,
  parseCount,
  parseDailyLimit,
  stoppedKey,
  timingSafeEqual,
  validateQuery,
  waitBeforeNext,
  withinDailyLimit,
} from "./core.mjs";
import { isFinalLabel, parsePage } from "./parse.mjs";

const ERROR_MESSAGES = {
  bad_request: "要求の内容に誤りがあります",
  unauthorized: "合言葉が無いか、違います",
  forbidden_origin: "このオリジンからの要求は許可されていません",
  not_found: "このパスはありません(/odds を使ってください)",
  method_not_allowed: "GET だけを受け付けます",
  closed: "締切後のため取得しません",
  daily_limit: "今日の取得上限に達しました",
  upstream_error: "公式サイトから取得できませんでした",
  stopped: "公式サイトから拒否されたため、今日は取得を停止しています",
  server_misconfigured: "Worker の設定(ODDS_KEY または ODDS_KV)が足りません",
};

/** 実際の Workers の環境で使う依存。テストではこれを差し替える。 */
function defaultDeps() {
  return {
    fetchFn: (url, init) => fetch(url, init),
    sleep: (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
    now: () => Date.now(),
    log: (obj) => console.log(JSON.stringify(obj)),
    makeResponse: (body, init) => new Response(body, init),
  };
}

export default {
  async fetch(request, env, ctx) {
    return handleRequest(request, env, defaultDeps());
  },
};

/**
 * 要求を処理する本体。request は method・url・headers.get を持つもの。
 * deps: {fetchFn, sleep, now, log, makeResponse, upstreamTimeoutMs(任意。テスト用)}
 */
export async function handleRequest(request, env, deps) {
  const startedMs = deps.now();
  const origin = request.headers.get("Origin");
  const allowed = parseAllowedOrigins(env.ALLOWED_ORIGINS);
  const corsHeaders = {};
  if (origin !== null) {
    // ブラウザからの要求。許可していないオリジンは断る(ブラウザ以外は Origin が無いので合言葉で守る)
    if (!isOriginAllowed(origin, allowed)) {
      return errorResponse(deps, 403, "forbidden_origin", {}, null);
    }
    corsHeaders["Access-Control-Allow-Origin"] = origin;
    corsHeaders["Vary"] = "Origin";
  }

  if (request.method === "OPTIONS") {
    return deps.makeResponse(null, {
      status: 204,
      headers: {
        ...corsHeaders,
        "Access-Control-Allow-Methods": "GET, OPTIONS",
        "Access-Control-Allow-Headers": "X-Odds-Key",
        "Access-Control-Max-Age": "600",
      },
    });
  }

  const url = new URL(request.url);
  if (url.pathname !== "/odds") return errorResponse(deps, 404, "not_found", corsHeaders, null);
  if (request.method !== "GET") return errorResponse(deps, 405, "method_not_allowed", corsHeaders, null);

  const kv = env.ODDS_KV;
  if (!env.ODDS_KEY || !kv) return errorResponse(deps, 500, "server_misconfigured", corsHeaders, null);
  const key = request.headers.get("X-Odds-Key");
  if (key === null || !timingSafeEqual(key, env.ODDS_KEY)) {
    return errorResponse(deps, 401, "unauthorized", corsHeaders, null);
  }

  const checked = validateQuery(url.searchParams, startedMs);
  if (!checked.ok) return errorResponse(deps, 400, "bad_request", corsHeaders, null, checked.message);
  const q = checked.value;
  const logBase = { race_id: q.raceId, types: q.types.join(",") };

  if (isClosed(q.closedAtMs, startedMs)) {
    deps.log({ ...logBase, result: "closed" });
    return errorResponse(deps, 409, "closed", corsHeaders, null, "締切後のため取得しません。確定オッズは翌日に反映されます");
  }

  if ((await kv.get(stoppedKey(q.date))) !== null) {
    deps.log({ ...logBase, result: "stopped" });
    return errorResponse(deps, 503, "stopped", corsHeaders, null);
  }

  const limit = parseDailyLimit(env.DAILY_LIMIT);
  const used = parseCount(await kv.get(countKey(q.date)));

  // キャッシュを先に見る(キャッシュから返した分は上限に数えない)
  const results = {}; // type → {status, fetched_at_ms, label, odds}
  const toFetch = [];
  for (const type of q.types) {
    const entry = await kv.get(cacheKey(q.raceId, type), { type: "json" });
    if (isCacheFresh(entry, startedMs)) {
      results[type] = { status: "cached", fetched_at_ms: entry.fetched_at_ms, label: entry.label ?? null, odds: entry.odds };
    } else {
      toFetch.push(type);
    }
  }

  if (toFetch.length > 0 && !withinDailyLimit(used, toFetch.length, limit)) {
    deps.log({ ...logBase, result: "daily_limit", used_today: used, limit_per_day: limit });
    return errorResponse(deps, 429, "daily_limit", corsHeaders, { used_today: used, limit_per_day: limit });
  }

  // 公式サイトから順番に取る(並列にしない)
  let sent = 0;
  let lastEndMs = null;
  let upstreamRefused = null;
  let finalSeen = Object.values(results).some((r) => isFinalLabel(r.label));
  const pageLogs = {};
  for (const type of toFetch) {
    if (finalSeen) {
      // 締切時オッズの表記を見たら、それ以上は取らない(spec/07 9.3 の4)
      results[type] = { status: "skipped" };
      continue;
    }
    const wait = waitBeforeNext(lastEndMs, deps.now());
    if (wait > 0) await deps.sleep(wait);
    const fetchStart = deps.now();
    sent++;
    const fetched = await fetchPage(deps, officialUrl(type, q.date, q.jcd, q.rno));
    lastEndMs = deps.now();
    pageLogs[type] = { http: fetched.status, ms: lastEndMs - fetchStart };
    if (fetched.status === 403 || fetched.status === 429) {
      upstreamRefused = fetched.status;
      break;
    }
    if (fetched.status !== 200 || fetched.text === null) {
      results[type] = { status: "error" };
      continue;
    }
    let parsed;
    try {
      parsed = parsePage(type, fetched.text);
    } catch (_e) {
      results[type] = { status: "parse_error" };
      continue;
    }
    results[type] = { status: "ok", fetched_at_ms: lastEndMs, label: parsed.label, odds: parsed.odds };
    if (isFinalLabel(parsed.label)) finalSeen = true;
    // 解析後の数値だけをキャッシュする(HTML は入れない)
    await kv.put(
      cacheKey(q.raceId, type),
      JSON.stringify({ fetched_at_ms: lastEndMs, label: parsed.label, odds: parsed.odds }),
      { expirationTtl: CACHE_TTL_SEC },
    );
  }

  const usedNow = used + sent;
  if (sent > 0) await kv.put(countKey(q.date), String(usedNow), { expirationTtl: COUNT_TTL_SEC });
  const quota = { used_today: usedNow, limit_per_day: limit };
  const pages = {};
  for (const type of q.types) pages[type] = results[type] ? results[type].status : "error";
  const elapsedMs = deps.now() - startedMs;

  if (upstreamRefused !== null) {
    // 相手から止められたサイン。その日の取得をすべて止める
    await kv.put(stoppedKey(q.date), JSON.stringify({ status: upstreamRefused, at: formatJstIso(deps.now()) }), {
      expirationTtl: STOPPED_TTL_SEC,
    });
    deps.log({ ...logBase, result: "stopped_now", upstream_status: upstreamRefused, pages, upstream: pageLogs, elapsed_ms: elapsedMs, used_today: usedNow });
    return errorResponse(deps, 503, "stopped", corsHeaders, quota);
  }

  const good = q.types.filter((t) => pages[t] === "ok" || pages[t] === "cached");
  if (good.length === 0) {
    deps.log({ ...logBase, result: "upstream_error", pages, upstream: pageLogs, elapsed_ms: elapsedMs, used_today: usedNow });
    return errorResponse(deps, 502, "upstream_error", corsHeaders, quota, undefined, { pages });
  }

  // 応答の組み立て(odds の形は Python の解析結果 spec/07 2.6 と同じ)
  const odds = {};
  const pagesFetchedAt = {};
  let oldestMs = null;
  let label = null;
  let anyCached = false;
  for (const type of good) {
    const r = results[type];
    Object.assign(odds, r.odds);
    pagesFetchedAt[type] = formatJstIso(r.fetched_at_ms);
    if (oldestMs === null || r.fetched_at_ms < oldestMs) oldestMs = r.fetched_at_ms;
    if (label === null && r.label !== null && r.label !== undefined) label = r.label;
    if (r.status === "cached") anyCached = true;
  }
  const nowMs = deps.now();
  const body = {
    ok: true,
    race_id: q.raceId,
    fetched_at: formatJstIso(oldestMs),
    cached: anyCached,
    cache_age_sec: anyCached ? Math.max(0, Math.floor((nowMs - oldestMs) / 1000)) : 0,
    pages,
    pages_fetched_at: pagesFetchedAt,
    odds_time_label: label,
    is_final_odds: good.some((t) => isFinalLabel(results[t].label)),
    odds,
    quota,
  };
  deps.log({ ...logBase, result: "ok", pages, upstream: pageLogs, elapsed_ms: nowMs - startedMs, used_today: usedNow });
  return jsonResponse(deps, 200, body, corsHeaders);
}

/** 1ページを取る。戻り値: {status: HTTPの状態 | null(タイムアウト・接続エラー), text}。 */
async function fetchPage(deps, url) {
  const controller = typeof AbortController === "function" ? new AbortController() : null;
  let timer = null;
  try {
    const timeout = new Promise((resolve) => {
      timer = setTimeout(() => {
        if (controller) controller.abort();
        resolve({ status: null, text: null, timeout: true });
      }, deps.upstreamTimeoutMs ?? UPSTREAM_TIMEOUT_MS);
    });
    const request = (async () => {
      const res = await deps.fetchFn(url, {
        method: "GET",
        headers: { "User-Agent": USER_AGENT, Accept: "text/html" },
        redirect: "manual",
        signal: controller ? controller.signal : undefined,
      });
      if (res.status !== 200) return { status: res.status, text: null };
      return { status: 200, text: await res.text() };
    })().catch(() => ({ status: null, text: null }));
    return await Promise.race([request, timeout]);
  } catch (_e) {
    return { status: null, text: null };
  } finally {
    if (timer !== null) clearTimeout(timer);
  }
}

function jsonResponse(deps, status, body, corsHeaders) {
  return deps.makeResponse(JSON.stringify(body), {
    status,
    headers: {
      ...corsHeaders,
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": "no-store",
    },
  });
}

function errorResponse(deps, status, code, corsHeaders, quota, message, extra) {
  const body = { ok: false, error: code, message: message ?? ERROR_MESSAGES[code] };
  if (quota) body.quota = quota;
  if (extra) Object.assign(body, extra);
  return jsonResponse(deps, status, body, corsHeaders);
}
