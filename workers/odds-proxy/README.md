# 締切前オッズの中継 Worker(odds-proxy)

スマホのアプリで「オッズを取る」を押したときに、そのレースの締切前オッズを公式サイト(boatrace.jp)から取り、
解析した数値をスマホにだけ返す Cloudflare Worker です。仕様は `spec/07_official_odds_and_settings.md` の 9章です。

- オッズの数値はスマホに返すだけです。リポジトリ・公開JSON・Worker のログには残しません。
- 公式サイトへの負荷を抑えるため、同じページは60秒使い回し、1日の取得は300ページ(既定)までにしています。
  公式サイトから 403・429 が返ったら、その日の取得をすべて止めます。

## ファイル

| ファイル | 内容 |
|---|---|
| `src/index.mjs` | 要求の処理(認証・CORS・入力チェック・締切・停止・キャッシュ・上限・順番の取得) |
| `src/parse.mjs` | HTML の解析(Python の `src/collect/official_odds.py` と同じ結果になる) |
| `src/core.mjs` | 日付・締切・キャッシュキー・上限などの小さな関数 |
| `test/*.test.mjs` | テスト(公式サイトにはアクセスしない) |
| `scripts/bench_parse.mjs` | 解析の所要時間の計測 |
| `wrangler.toml.example` | 設定の雛形 |

ファイルの拡張子が `.mjs` なのは、package.json を置かずに Node でも ES modules として読ませるためです(Workers ではそのまま動きます)。

---

## デプロイの手順(初めての人向け)

全体の流れ: ① Cloudflare のアカウントを作る → ② Node 22 を用意する → ③ ログインする → ④ 設定ファイルを作る →
⑤ KV を作る → ⑥ 合言葉を設定する → ⑦ 許可するオリジンを決める → ⑧ デプロイ → ⑨ 動作を確かめる → ⑩ スマホに設定する

### ① Cloudflare のアカウントを作る

1. <https://dash.cloudflare.com/sign-up> を開き、メールアドレスとパスワードで登録します(無料。クレジットカードは要りません)。
2. 届いたメールのリンクを開いて、メールアドレスを確認します。
3. ダッシュボードの左のメニューで「Workers & Pages」(Compute の中にある場合もあります)を開きます。
   初回は workers.dev のサブドメイン(`<好きな名前>.workers.dev`)を決める画面が出るので、名前を決めます。
   この名前が URL の `<アカウント>` の部分になります。
4. プランは「Free」のままで使えます。

### ② Node 22 を用意する(WSL で)

デプロイに使う `wrangler`(Cloudflare の公式ツール)は Node 22 以上が必要です。このPCの Node は 16 なので、
nvm で Node 22 を追加します(sudo は不要です。`spec/07` の Q7 で利用者の確認が必要な項目です)。

```bash
nvm install 22      # 初回だけ。Node 22 を入れる
nvm use 22          # このターミナルで Node 22 を使う(終わったら `nvm use 16` で戻せる)
node --version      # v22.x.x と出れば OK
```

wrangler はリポジトリには入れません。毎回 `npx wrangler ...` で呼び出します(初回は「インストールしますか?」と聞かれるので `y`)。

### ③ Cloudflare にログインする

```bash
cd ~/workspace/boatrace-yoso/workers/odds-proxy
npx wrangler login
```

ブラウザが開くので「Allow」を押します。WSL でブラウザが開かないときは、表示された URL を Windows のブラウザに貼ってください。
`npx wrangler whoami` でアカウント名が出ればログインできています。

### ④ 設定ファイルを作る

```bash
cp wrangler.toml.example wrangler.toml
```

`wrangler.toml` には秘密の値は入りませんが、KV の id や自宅LANのアドレスを書くので、**commit しないでください**
(`workers/odds-proxy/wrangler.toml` と `workers/odds-proxy/.wrangler/` を `.gitignore` に入れるよう PM に依頼している。入っていなければ commit 前に確かめる)。

### ⑤ KV を作る

KV は、キャッシュ・1日の使用数・停止フラグを置く小さな保存場所です。

```bash
npx wrangler kv namespace create ODDS_KV
```

出力に `id = "0123abcd..."` のような行が出るので、その値を `wrangler.toml` の `id = "<ここに KV の id を貼る>"` に貼ります。

### ⑥ 合言葉を設定する

合言葉は、他人が勝手にこの Worker を使えないようにするためのものです。長いランダムな文字列にします。

```bash
node -e "console.log(require('crypto').randomBytes(24).toString('base64url'))"   # 合言葉を作る(表示された文字列を控える)
npx wrangler secret put ODDS_KEY                                                  # 聞かれたら、作った合言葉を貼る
```

- 合言葉は Cloudflare の中に暗号化して保存され、`wrangler.toml` やリポジトリには残りません。
- 同じ合言葉を、あとでスマホのアプリの設定画面に入力します(⑩)。
- 変えたいときは、もう一度 `npx wrangler secret put ODDS_KEY` を実行します。

### ⑦ 許可するオリジンと1日の上限を決める

`wrangler.toml` の `[vars]` を書き換えます。

```toml
ALLOWED_ORIGINS = "http://localhost:8000,http://127.0.0.1:8000,http://192.168.1.10:8000"
DAILY_LIMIT = "300"
```

- **オリジン**は「アプリを開いている URL の、パスより前の部分」です。アプリを開くアドレスごとに足します。
  - PCのブラウザで `http://localhost:8000/` から開く → `http://localhost:8000`
  - 自宅Wi-Fiのスマホで `http://192.168.1.10:8000/` から開く(ローカル表示)→ `http://192.168.1.10:8000`
    (PCのIPは WSL ではなく Windows 側のもの。`ipconfig` の「IPv4 アドレス」。変わらないよう、ルーターでPCのIPを固定すると安心です)
  - GitHub Pages を公開したら → `https://<GitHubのユーザー名>.github.io`(後ろの `/boatrace-yoso` は付けない)
- 許可していないオリジンからの要求は 403(`forbidden_origin`)になります。
- **DAILY_LIMIT** は公式サイトから取るページ数の1日の上限です(1レース3ページなので、300で100レース分)。
  KV の書き込みの無料枠(1,000回/日)を超えないよう、700より大きくしても700に切り詰めます。

### ⑧ デプロイする

```bash
npx wrangler deploy
```

最後に `https://odds-proxy.<アカウント>.workers.dev` のような URL が出ます。これが Worker の URL です。
設定(`wrangler.toml`)を変えたときも、もう一度 `npx wrangler deploy` を実行します。

### ⑨ 動作を確かめる

以下では、Worker の URL と合言葉をシェルの変数に入れておきます。

```bash
W=https://odds-proxy.<アカウント>.workers.dev
K='<合言葉>'
```

**(a) 公式サイトにアクセスしない確認**(何回やっても公式サイトには行きません)

```bash
curl -i "$W/odds"                                     # 401 unauthorized(合言葉なし)
curl -i -H "X-Odds-Key: $K" "$W/odds?date=20000101&jcd=01&rno=1&closed_at=2000-01-01T10:00:00%2B09:00"
                                                      # 400 bad_request(今日以外の日付)。合言葉が正しい証拠
curl -i -X OPTIONS -H "Origin: http://localhost:8000" "$W/odds"
                                                      # 204 と Access-Control-Allow-Origin が返る(CORS の確認)
curl -i -H "Origin: https://example.com" -H "X-Odds-Key: $K" "$W/odds"
                                                      # 403 forbidden_origin
```

**(b) 公式サイトに接続できるかの最初の確認(大事)**

Cloudflare のデータセンターからの接続を、公式サイトが拒否する可能性があります(`spec/07` 9.4 の未確認のリスク)。
最初は **単勝・複勝の1ページだけ**(`types=tf`)で、**1回だけ** 試します。

1. 別のターミナルでログを流しておきます: `npx wrangler tail`
2. 今日、まだ締切前のレースを1つ選びます(アプリのレース一覧で、場番号・R番号・締切時刻を見る)。
   例: 戸田(02)の 5R、締切 14:05 なら、

```bash
D=$(TZ=Asia/Tokyo date +%Y%m%d)
time curl -s -H "X-Odds-Key: $K" \
  "$W/odds?date=$D&jcd=02&rno=5&types=tf&closed_at=$(TZ=Asia/Tokyo date +%Y-%m-%d)T14:05:00%2B09:00"
```

結果の見方:

| 結果 | 意味 | すること |
|---|---|---|
| `"ok":true` と `"pages":{"tf":"ok"}`、`odds` に数値 | 接続できた | `time` の秒数を控える(10秒前後の見込み)。次に `types` を外して3ページで1回試し、30秒前後で返ることを確かめる |
| 409 `closed` | 締切後のレースを指定した | 締切前のレースで試し直す |
| 502 `upstream_error`、`pages` が `error` | 公式サイトから応答が無い・5xx | `wrangler tail` のログの `upstream` の `http`(状態)と `ms`(時間)を見る。`http: null` はタイムアウト(13秒)か接続エラー。数分あけて1回だけ再試行し、それでも同じなら下の「拒否された場合」へ |
| 502 で `pages` が `parse_error` | 取れたが、HTML の形が想定と違う(エラー画面など) | 何度も試さず、中継担当(R)に連絡する |
| 503 `stopped` | 公式サイトが 403 か 429 を返した(拒否された) | 下の「拒否された場合」へ。**繰り返し試さないこと** |

**拒否された場合(503 stopped、または 502 が続く)**

- Worker はその日の取得を自動で止めています(KV に `stopped:YYYYMMDD` が書かれ、翌日になるまで公式サイトに行きません)。
- その日は再試行しません。翌日にもう1回だけ試し、それでも同じなら **Cloudflare 経由は使えない** と判断し、
  PC常駐ループとローカル表示(`spec/07` 2章・5章、`scripts/live_loop.py`)に戻します(`spec/07` Q9)。
  Worker は「⑪ 止め方」の手順で止めておきます。
- 停止を手動で解除したいとき(原因がわかって、もう一度だけ試すとき):

```bash
npx wrangler kv key list --binding ODDS_KV --remote                       # 今あるキーを見る
npx wrangler kv key delete --binding ODDS_KV --remote "stopped:20261103"  # 停止フラグを消す(日付は今日に)
```

(古い wrangler では `kv key` が `kv:key` という書き方です。`--remote` を付けないと、手元の模擬の KV を操作してしまいます)

### ⑩ スマホのアプリに設定する

アプリの設定画面(S6)で、Worker の URL(`https://odds-proxy.<アカウント>.workers.dev`)と合言葉を入力します。
レース詳細の「オッズを取る」で取得できれば完了です(タスク P-3)。

### ⑪ 止め方

- **今日だけ止める**: 停止フラグを手で書く
  `npx wrangler kv key put --binding ODDS_KV --remote "stopped:$(TZ=Asia/Tokyo date +%Y%m%d)" manual`
- **しばらく止める**: ダッシュボードの Workers & Pages → odds-proxy → Settings → Domains & Routes で workers.dev を無効にする
  (URL にアクセスできなくなる。もう一度有効にすれば戻る)
- **完全に消す**: `npx wrangler delete`(KV は残るので、要らなければ `npx wrangler kv namespace delete --binding ODDS_KV`)

---

## 無料枠の使用量の確認

| 何を | どこで |
|---|---|
| 要求数・エラー数・**CPU時間** | ダッシュボード → Workers & Pages → odds-proxy → Metrics(CPU Time の中央値・99%) |
| KV の読み書きの回数 | ダッシュボード → Storage & Databases → KV(Workers KV)→ ODDS_KV → Metrics |
| 今日の公式サイトへの取得ページ数 | 応答の `quota.used_today`、または `npx wrangler kv key get --binding ODDS_KV --remote "count:YYYYMMDD"` |
| ログ(オッズの数値は出ない) | `npx wrangler tail`、またはダッシュボードの Logs |

無料枠の見込み(`spec/07` 9.4): 要求 10万/日、CPU 10ms/要求、KV 書き込み 1,000/日。
1回の取得で KV の書き込みは最大4回(キャッシュ3 + 使用数1)なので、上限300ページなら約400回/日です。

## CPU 時間(解析の所要時間)の計測

`node workers/odds-proxy/scripts/bench_parse.mjs`(2026-11-03、このPCの WSL・Node 16.20、実物のHTML 5ページ、各300回):

| ページ | 大きさ | 初回 | 中央値 | 95% |
|---|---|---|---|---|
| odds3t(3連単) | 43〜44KB | 0.47〜0.64ms | 0.18〜0.20ms | 0.33〜0.42ms |
| odds2tf(2連単・2連複) | 37KB | 0.14〜1.13ms | 0.11ms | 0.23〜0.25ms |
| oddstf(単勝・複勝) | 33KB | 0.24ms | 0.06ms | 0.10ms |

1要求(3ページ)でも数ms以内で、無料枠の 10ms に収まる見込みです。
Workers の中では `performance.now()` が CPU の実行中に進まないため、Worker 自身では測れません。
デプロイ後はダッシュボードの Metrics の CPU Time で確かめてください。

## ローカルのテスト

リポジトリ直下で(Node 16 で動きます。公式サイトにはアクセスしません):

```bash
node --test workers/odds-proxy/test/                 # まとめて実行(Node 16 ではファイル単位の結果だけ出る)
node workers/odds-proxy/test/handler.test.mjs        # 1ファイルずつ実行すると、テストごとの結果が出る
```

- `parse.test.mjs`: 共通の合成HTML(`tests/fixtures/official_odds/`、収集担当が作る)が `*.expected.json` と一致すること、
  壊れた例(`broken_*.html`)で失敗すること。実物のHTML(`data/raw/official_odds/`、git管理外)があれば、それも解析できること。
  **Python 側の解析を直したら、このテストも必ず通すこと**(逆も同じ)。
- `core.test.mjs`: 日付・締切・入力チェック・上限・合言葉の比較・オリジン。
- `handler.test.mjs`: 偽物の fetch・KV・時計で、各ステータスコード、キャッシュ、ページの間隔、停止、ログの中身。

---

## API(フロント担当向けの要約)

```
GET {Worker の URL}/odds?date=YYYYMMDD&jcd=02&rno=1&types=3t,2tf,tf&closed_at=2026-11-03T10:45:00%2B09:00
X-Odds-Key: <合言葉>
```

- `date` は日本時間の今日だけ。`jcd` は `01`〜`24` の2桁。`rno` は `1`〜`12`。
- `types` は `3t`・`2tf`・`tf` のカンマ区切り(省略で3つすべて。重複は無視、順番は常に 3t→2tf→tf)。
- `closed_at` は公開JSONの `closed_at` をそのまま(URL では `+` を `%2B` にする。`encodeURIComponent` を使えばよい)。
  時差が無い `2026-11-03 10:45:00` も日本時間として受け付ける。`date` と違う日付なら 400。

成功(200):

```json
{
  "ok": true,
  "race_id": "20261103-02-01",
  "fetched_at": "2026-11-03T10:40:12+09:00",
  "cached": false,
  "cache_age_sec": 0,
  "pages": {"3t": "ok", "2tf": "ok", "tf": "ok"},
  "pages_fetched_at": {"3t": "2026-11-03T10:40:12+09:00", "2tf": "2026-11-03T10:40:23+09:00", "tf": "2026-11-03T10:40:34+09:00"},
  "odds_time_label": "オッズ更新時間 10:40",
  "is_final_odds": false,
  "odds": {"trifecta": {}, "exacta": {}, "quinella": {}, "win": {}, "place": {}},
  "quota": {"used_today": 123, "limit_per_day": 300}
}
```

- `odds` の形は Python の解析結果(`spec/07` 2.6)と同じ。取れなかった組合せ(欠場・票なしの `0.0`)は `null`。
  `quinella` のキーは小さい方の艇(`"1"`〜`"5"`)。`place` は `{"lower_limit", "upper_limit"}` か `null`。
- `pages` の値: `ok`(今回取った)/ `cached`(60秒以内に取ったものを返した)/ `error`(5xx・タイムアウト・接続エラー)/
  `parse_error`(HTML の形が違う)/ `skipped`(先のページで「締切時オッズ」を見たので取らなかった)。
  一部だけ失敗しても 200。`ok` と `cached` の券種だけが `odds` に入る。
- `fetched_at` は使ったページのうち最も古い取得時刻(キャッシュでも元の時刻)。`cache_age_sec` はその経過秒数(キャッシュを使わなければ 0)。
  ページごとの時刻は `pages_fetched_at`。
- `odds_time_label` は公式の表記(締切前「オッズ更新時間 HH:MM」、締切後「締切時オッズ」。無ければ `null`)。
  `is_final_odds` はどれかのページが「締切時」なら `true`。

失敗: `{"ok": false, "error": "<コード>", "message": "<日本語の説明>"}`(429・502・503 には `quota` も付く。502 には `pages` も付く)

| HTTP | `error` | 場面 |
|---|---|---|
| 400 | `bad_request` | パラメータの誤り・今日以外の日付(`message` に理由) |
| 401 | `unauthorized` | 合言葉が無い・違う |
| 403 | `forbidden_origin` | 許可していないオリジン(この応答には CORS のヘッダが付かないので、ブラウザでは中身を読めず、fetch が失敗したように見える) |
| 404 | `not_found` | `/odds` 以外のパス |
| 405 | `method_not_allowed` | GET・OPTIONS 以外 |
| 409 | `closed` | `closed_at` を過ぎている |
| 429 | `daily_limit` | 1日の上限に達した(取りに行く必要のあるページ数を足すと上限を超える) |
| 500 | `server_misconfigured` | Worker の設定不足(合言葉か KV が未設定) |
| 502 | `upstream_error` | どのページも取れなかった(5xx・タイムアウト・解析の失敗) |
| 503 | `stopped` | 公式サイトから 403・429 を受けて、その日の取得を止めている |

- 1回の要求で最大3ページを順番に取るので、応答まで最大30〜40秒かかります(1ページの待ち時間の上限は13秒)。スマホ側のタイムアウトは45秒にしてください。
- 応答には `Cache-Control: no-store` を付けています。

## 仕様(spec/07 9章)からの変更点・補足

- ソースの拡張子を `.js` ではなく `.mjs` にした(package.json を置かずに Node 16 でテストするため)。
- HTML の切り出しは「最初の `<div class="table1">`」ではなく、「最初の `oddsPoint` の位置から最後のセルの `</td>` まで」にした。
  最初の `table1` はレース情報の表(`table1 h-mt10`)なので。オッズ時点は `p.tab4_refreshText`(締切前、複数行)と `p.tab4_time`(締切後)の両方を読む。
- セルは `td` の class に `oddsPoint` という語を含むもの(`"oddsPoint "` のような末尾の空白や、他の class との併記も可)。
  Python の HTMLParser と同じく、中の `<span>` などのタグは無視して文字だけを取り、閉じ忘れの `td` も扱う。
- `pages` の状態に `skipped` を追加。応答に `pages_fetched_at` を追加。404・405・500 を追加。
- `DAILY_LIMIT` は700で切り詰める(KV の書き込み上限のため)。
- 上限の判定は「今日の使用数 + 今回取りに行くページ数 ≦ 上限」。キャッシュから返すページは数えない。失敗した取得も1ページとして数える。
- 公式サイトへのリトライはしない(締切に間に合わせるため。スマホ側の再試行ボタンで1回やり直す)。
- KV は結果整合なので、2台から同時に押すと、ページの間隔(1秒)と上限の数え方が少しずれることがある(目安として扱う)。
- Cache API は使わず KV だけにした(仕様どおり)。
