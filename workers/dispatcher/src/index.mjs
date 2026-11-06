// 定刻に GitHub Actions の daily ワークフローを起動する Cloudflare Worker。
// GitHub の定期実行(schedule)は遅れや取りこぼしがあるため、Cloudflare の Cron Trigger から
// workflow_dispatch を呼ぶ。トークン(GH_TOKEN)は secret。ログにトークンは出さない。
// 負けたあとに賭け金を増やすなどの処理とは無関係(起動するだけ)。

export const REPO = "misaki777/boatrace-yoso";
export const WORKFLOW = "daily.yml";

// Cron 式(UTC) → モード。wrangler.toml の [triggers] crons と同じ文字列にすること。
export const CRON_MODES = {
  "41 22 * * *": "morning", // JST 7:41
  "4,34 23,0-14 * * *": "daytime", // JST 8:04 〜 23:34 の30分ごと
  "12 16 * * *": "finalize", // JST 1:12(前日分の確定)
  "0 18 * * SUN": "weekly", // 月曜 JST 3:00(週の分析)
};

export function modeForCron(cron) {
  return CRON_MODES[cron] || null;
}

export function dispatchRequest(mode, token) {
  return {
    url: `https://api.github.com/repos/${REPO}/actions/workflows/${WORKFLOW}/dispatches`,
    init: {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
        Accept: "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "br-yoso-dispatcher",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ ref: "main", inputs: { mode, date: "", deploy: true } }),
    },
  };
}

export async function dispatch(mode, env, fetchFn = fetch) {
  if (!env.GH_TOKEN) {
    console.log(JSON.stringify({ event: "dispatch", mode, ok: false, error: "GH_TOKEN が設定されていません" }));
    return { ok: false, status: 0 };
  }
  const { url, init } = dispatchRequest(mode, env.GH_TOKEN);
  const res = await fetchFn(url, init);
  // 成功は 204。失敗時は本文の先頭だけ記録する(トークンは含まれない)
  const detail = res.status === 204 ? "" : (await res.text()).slice(0, 200);
  console.log(JSON.stringify({ event: "dispatch", mode, ok: res.status === 204, status: res.status, detail }));
  return { ok: res.status === 204, status: res.status };
}

export default {
  async scheduled(event, env, ctx) {
    const mode = modeForCron(event.cron);
    if (!mode) {
      console.log(JSON.stringify({ event: "dispatch", ok: false, error: `不明な cron: ${event.cron}` }));
      return;
    }
    ctx.waitUntil(dispatch(mode, env));
  },
  // 動作確認用: 状態だけ返す(起動はしない。トークンの有無だけを示す)
  async fetch(request, env) {
    return new Response(JSON.stringify({ ok: true, has_token: Boolean(env.GH_TOKEN), crons: CRON_MODES }), {
      headers: { "content-type": "application/json; charset=utf-8" },
    });
  },
};
