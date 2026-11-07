# 直前情報の取得 Worker(br-yoso-beforeinfo)

Cloudflare の Cron Trigger で5分ごとに起動し、締切の15〜10分前のレースについて、公式サイトの直前情報ページ
(`/owpc/pc/race/beforeinfo`)を1回だけ取得・解析して R2 に保存し、GitHub Actions(`daily.yml` の `mode=lock`)を起動します。
仕様は `spec/12_beforeinfo.md`。解析は収集担当の `src/collect/beforeinfo.py` と同じ結果になります。

## 動き(1回の起動)

1. R2 `br-yoso-data` の `beforeinfo/YYYYMMDD/_stopped.json` があれば何もしない。
2. `schedule/YYYYMMDD.json`(朝ジョブが置く当日のレース一覧と締切時刻)を読む。無ければ終わる。
3. 締切の **15〜10分前**(`WINDOW_START_MIN`〜`WINDOW_END_MIN`)で、まだ取っていないレースを締切の早い順に選ぶ。
   1回の起動で取るのは **最大3ページ**。あふれたレースは次の起動で、締切の **5分前**(`LAST_CHANCE_MIN`)まで取る。
   1レース1回だけで、未掲載(`not_posted`)でも取り直さない。4分前以降・締切後は取らない。
4. 1件ずつ順番に、前の応答の終わりから **1.5秒以上** 空けて取る(1ページの待ち時間の上限 20秒)。
5. 解析結果の JSON だけを `beforeinfo/YYYYMMDD/SS-RR.json` に保存(HTML は保存しない)。
   形: `{race_id, fetched_at, minutes_before_close, attempt, status, weather, boats}`(`status` は ok / partial / not_posted)。
6. 保存したレースがあれば `workflow_dispatch` を **1回だけ** 送る:
   `{"ref":"main","inputs":{"mode":"lock","race_ids":"20261106-01-05,...","date":"","deploy":true}}`。
   前回の送信から4分未満なら送らず、次の起動でまとめて送る。送れなかった分も次に回す。
7. 状態(使用数・連続失敗数・前回の送信時刻・送信待ちの race_id・取得済みのレース)を `beforeinfo/YYYYMMDD/_state.json` に書く。

止まる条件(`_stopped.json` を書き、その日は以後何もしない):

- 公式サイトが **403・429** を返した
- **3回続けて** 失敗した(5xx・タイムアウト・解析エラー。起動をまたいで数える)
- 1日の使用数が **DAILY_LIMIT(既定300)** に達した

再開は、原因を確かめてから利用者が `_stopped.json` を消す(下の「止める・再開する」)。

ログ(`wrangler tail`): 1ページごとに `race_id`・`status`・`minutes_before_close`・所要時間、起動ごとに件数・使用数・結果。
**トークン・展示タイムなどの値・HTML は出さない**(テストで確認)。稼働後は `status` と `minutes_before_close` を見て時間帯を調整する。

## 締切前オッズの記録(同じ Worker に同居。spec/13)

無料プランの Cron Trigger が上限(5つ)のため、同じ5分ごとの起動の中で、直前情報の後に動きます(`src/odds.mjs`)。

- 対象: schedule の全レース。締切の **8〜3分前**(`ODDS_WINDOW_START_MIN`〜`ODDS_WINDOW_END_MIN`)に、
  単勝・複勝(`oddstf`)と2連単・2連複(`odds2tf`)を **1回ずつ** 取る。3連単は `ODDS_TYPES="tf,2tf,3t"` で追加(既定は取らない)。
  失敗したページも取り直さない。
- 保存: R2 の `odds/YYYYMMDD/SS-RR.json`(非公開)。HTML は保存しない。形:
  `{race_id, closed_at, pages: {tf: {status, fetched_at, minutes_before_close, odds_time_label, is_final_odds}, ...}, odds: {win, place, exacta, quinella[, trifecta]}}`。
  `odds` の形は odds-proxy・Python(`spec/07` 2.6)と同じ。取れたページが1つも無いレースは保存しない(状態にだけ残す)。
- 解析は `workers/odds-proxy/src/parse.mjs` をそのまま import(共通 fixture `tests/fixtures/official_odds/` で Python と一致を確認済み。
  wrangler が1つにまとめてデプロイする)。
- 優先順位と1回の起動のページ数: **直前情報が先**(最大3ページ)、残りの枠(合計 `TICK_PAGE_LIMIT`、既定5)でオッズを **締切が近い順** に。
  間隔は両方を通して1.5秒以上。1回の起動は最大 約5×(10秒+1.5秒) ≒ 1分(Cron の起動は15分まで動ける)。CPU は1ページ1ms未満の見込み。
- 上限・停止は直前情報と **別**: 1日 `ODDS_DAILY_LIMIT`(既定300)ページ、公式の 403/429、3回続けての失敗で
  `odds/YYYYMMDD/_stopped.json` を書き、その日の **オッズの取得だけ** を止める(直前情報は続ける。逆も同じ)。
  状態は `odds/YYYYMMDD/_state.json`。再開は `_stopped.json` を消す(下の「止める・再開する」のパスを `odds/` に替える)。
- ログ: `"event":"odds_page"` に race_id・券種・状態・締切までの分・所要時間だけ。オッズの数値・公式の表記は出さない。
- `GET /` の状態に `odds_used_today`・`odds_limit_per_day`・`odds_stopped` を追加。
- 公式へのアクセスは、直前情報 約150〜300 + オッズ 約300(2ページ×約150レース)ページ/日。odds-proxy(スマホからの取得)とは別に数える。

注意: 窓の既定は 8〜3分前(指示の例は 7〜4分前)。Cron が5分ごとなので、幅が5分より狭いと窓に入らないレースが出るため。

## ファイル

| ファイル | 内容 |
|---|---|
| `src/index.mjs` | Cron の処理(`runTick`)と GitHub への dispatch。`fetch` は状態の要約だけを返す(取得しない) |
| `src/parse.mjs` | HTML の解析(Python の `parse_beforeinfo` と同じ切り出し方・正規表現) |
| `src/odds.mjs` | 締切前オッズの記録(窓・上限・停止・保存) |
| `src/core.mjs` | 時刻・窓の判定・R2 のキー・上限などの小さな関数 |
| `test/*.test.mjs` | テスト(公式サイト・GitHub にはアクセスしない) |
| `wrangler.toml.example` | 設定の雛形 |

## テスト

リポジトリ直下で(Node 16 で動く):

```bash
node --test workers/beforeinfo/test/
node workers/beforeinfo/test/tick.test.mjs   # 1ファイルずつだとテストごとの結果が出る
```

- `parse.test.mjs`: 共通の合成HTML `tests/fixtures/beforeinfo/`(収集担当 C-8)の6例(ok・partial・not_posted・flying・absent・maeduke)が
  `*.expected.json` と完全に一致すること。**Python 側の解析を直したら、このテストも必ず通すこと**(逆も同じ)。
- `tick.test.mjs`: 窓の外は取らない、取り直さない、3ページの上限、1.5秒の間隔、dispatch は1回の起動で1回・4分以内は送らない、
  403/429・3連続失敗・上限で止まる、ログに値やトークンが出ない、時間帯の設定。

解析の所要時間(Node 16、合成HTML 14〜17KB、各300回): 中央値 0.06〜0.11ms、95% 0.13〜0.44ms。
実物は約60KBなので数倍になるとしても、1回の起動(最大3ページ)で数ms以内の見込みです。
デプロイ後は、ダッシュボードの Metrics の CPU Time で確かめてください(Workers の中では時間を測れません)。

## 無料枠

| 制約(無料プラン) | 見込み |
|---|---|
| **Cron Trigger: アカウントで5つまで** | dispatcher が4つ使っているので、この Worker の1つで **5つ目(上限ちょうど)**。これ以上は増やせない。足りなくなったら、この処理を dispatcher の Worker に同居させる(`scheduled` で `event.cron` によって分ける) |
| 起動回数 | 192回/日(JST 8:00〜23:55 の5分ごと) |
| CPU 10ms/起動 | 解析は最大3ページ、1ページ1ms未満の見込み |
| サブリクエスト 50/起動 | 公式 最大3 + GitHub 1 + R2 数回 |
| R2 の書き込み | 1日 約300〜450(結果JSON 約150〜300 + 状態) |

公式サイトへのアクセスは 1日 約150〜300ページ(上限300)。オッズ中継(odds-proxy、上限300)とは別に数えます。

## デプロイの手順

前提: Node 20 以上と `npx wrangler`、Cloudflare にログイン済み(`workers/odds-proxy/README.md` の②③と同じ)。
R2 のバケット `br-yoso-data` は既にある前提です(CI 担当が使っているもの)。

```bash
cd ~/workspace/boatrace-yoso/workers/beforeinfo
cp wrangler.toml.example wrangler.toml       # commit しない(.gitignore に入れるよう PM に依頼)
npx wrangler secret put GH_TOKEN             # dispatcher と同じトークンを貼る(Actions を起動する権限)
npx wrangler deploy
```

### 動作の確かめ方

1. `curl https://br-yoso-beforeinfo.<アカウント>.workers.dev/` で状態の要約が返る
   (`has_token`・`has_schedule`・`used_today`・`stopped`。値やトークンは返さない。公式サイトにはアクセスしない)。
2. 開催中の時間帯に `npx wrangler tail` でログを見る。最初の取得で:
   - `"event":"page"` に `"status":"ok"` か `partial` / `not_posted` → 接続できている。R2 に `beforeinfo/YYYYMMDD/SS-RR.json` ができる。
   - `"event":"dispatch","ok":true` → GitHub の Actions に `lock` の実行が出る。`status: 401/403/404` ならトークンの権限か、ワークフロー・モード名を確認。
   - `http_403` / `http_429` → 公式サイトに拒否された。その日は自動で止まる。**再開を急がず**、翌日に1回だけ様子を見る。
     続くようなら、この Worker を止めて Open API の30分ごとの取得に戻す(spec/12 の予備)。
3. ダッシュボードの Workers → br-yoso-beforeinfo → Settings → Triggers に Cron が1つ出ていること。
   Cron の数の上限に当たってデプロイが失敗した場合は、上の「無料枠」の同居の方法で対応する。

### 止める・再開する

```bash
# 今日だけ止める
echo '{"reason":"manual"}' > /tmp/stop.json
npx wrangler r2 object put br-yoso-data/beforeinfo/$(TZ=Asia/Tokyo date +%Y%m%d)/_stopped.json --file /tmp/stop.json --remote
# 再開する(原因を確かめてから)
npx wrangler r2 object delete br-yoso-data/beforeinfo/$(TZ=Asia/Tokyo date +%Y%m%d)/_stopped.json --remote
```

しばらく止めるときは、`wrangler.toml` の `crons = []` にして `npx wrangler deploy`(または `npx wrangler delete` で削除)。

## spec/12 からの変更点(利用者の決定 2026-11-06 などによる)

- **2回目の取得はしない**: 締切の15〜10分前に1レース1回だけ。未掲載でも取り直さない(spec 1.2 の2回目は廃止)。
- **1日の上限は 300**(spec 2.1 は400)。達したらその日は止める(「2回目をやめる」段階は無くなった)。
- 3ページの上限で回されたレースだけは、窓を過ぎても締切の5分前まで取る(取りこぼしを防ぐため。1回だけなのは同じ)。
- 取得の時間帯は環境変数(`WINDOW_START_MIN` / `WINDOW_END_MIN` / `LAST_CHANCE_MIN`)で変えられる(実ページで締切21分前は未掲載だったため。稼働後の実績で調整)。
- dispatch の入力は `{mode: "lock", race_ids, date: "", deploy: true}`(CI 担当の daily.yml に合わせた)。
- GitHub のトークンは dispatcher と同じものを secret `GH_TOKEN` に入れる(spec 1.4 の `GITHUB_DISPATCH_TOKEN` から名前を変更)。
- 解析結果の JSON は Python の解析結果に合わせ、spec 3章の例に加えて `racer_number`・`start_flying`・`absent`・`weather.as_of` を持つ。
  `parts` は Open API と同じ `[{number_source, number, quantity}]`。取得に失敗したレースの JSON は保存しない(状態にだけ `failed` と残す)。
- ソースの拡張子は `.mjs`(package.json を置かずに Node 16 でテストするため。spec の `parse.js` は `src/parse.mjs`)。
