# 01 収集仕様(src/collect/)

担当: 収集担当。触ってよい範囲は `06_tasks.md` の「ファイル境界」を参照。

## 1. 対象ソース

| ソースID | URLテンプレート | 用途 | 期間 |
|---|---|---|---|
| `turnmark` | `https://turnmark.github.io/api/v1/{YYYY}/{YYYYMMDD}.json` | 過去日の正本(出走表・直前・結果・払戻・確定オッズ) | 2026-01-01〜前日 |
| `openapi` | `https://boatraceopenapi.github.io/api/v1/{YYYY}/{YYYYMMDD}.json` | 当日分(予想と当日の結果反映)。turnmarkが欠けた日の代わり | 2025-12-31〜当日 |
| `official_odds`(Phase 2) | `https://www.boatrace.jp/owpc/pc/race/{odds3t,odds2tf,oddstf}?rno=&jcd=&hd=` | 締切前オッズ。**設計は `07_official_odds_and_settings.md` 2章**(待機・停止条件・保存先は07が優先する) | 当日のみ |
| `legacy`(Phase 4) | `https://boatraceopenapi.github.io/{programs,previews,results}/v3/{YYYY}/{YYYYMMDD}.json` | 長期の学習データ | 2016-08-01〜 |

- `today.json` は使わず、日付を指定したURLを使う(日付の取り違えを防ぐため)。
- 1日1ファイルで全場分が入っている。1日あたりの非圧縮サイズは約2〜3MB(gzipで約0.3MB)。
- 2026-01-01〜2026-11-01 の一括取得は turnmark で305リクエスト。1.5秒間隔なら約8分、保存容量はgzipで約90MB。

## 2. 保存先と命名

```
data/raw/
  turnmark/2026/20261101.json.gz
  openapi/2026/20261102.json.gz
  legacy/{programs,previews,results}/2016/20160801.json.gz   (Phase 4)
  _fetch_log.jsonl                                           (全ソース共通の取得ログ)
```

- 中身はレスポンスのバイト列をそのまま gzip で保存する(再整形しない。indent=2で書き直すとサイズが3倍以上になるため)。
- 書き込みは一時ファイル `*.tmp` に書いてから rename する(途中で止まっても壊れたファイルを残さない)。
- 既存の `data/raw/20261102.json` は、移行タスク(C-1)で `data/raw/openapi/2026/20261102.json.gz` に移す。
- 読み込みは共通関数 `src/collect/raw_io.py: load_raw(source, date) -> dict | None` を使う(収集担当が作る。他の担当は import して使うだけ)。

### 取得ログ `_fetch_log.jsonl`(1行1リクエスト、追記のみ)

```json
{"source": "openapi", "date": "2026-11-02", "url": "https://...", "status": 200, "bytes": 2748036, "sha256": "…", "fetched_at": "2026-11-02T18:20:11+09:00", "final": false}
```

- `final`: 取得した時点で、そのデータが確定済みとみなせるか。
  - turnmark: 200 が返れば true。
  - openapi: `fetched_at >= 対象日の翌日 01:00 JST` なら true(0時の取り直しを待つ)。
  - legacy: 対象日の翌々日以降に取得していれば true。

## 3. CLI

既存の `src/collect/fetch_openapi.py` は単日用なので、新しく `src/collect/fetch.py` を作って置き換える(古いファイルは C-1 で削除するか、薄いラッパーとして残す)。

```
python -m src.collect.fetch --source turnmark --from 2026-01-01 --to 2026-11-01
python -m src.collect.fetch --source openapi  --date 2026-11-02          # 当日分(再取得あり)
python -m src.collect.fetch --source auto     --from 2026-10-25 --to 2026-11-02
```

| オプション | 既定 | 説明 |
|---|---|---|
| `--source` | `auto` | `turnmark` / `openapi` / `auto`。auto は前日以前を turnmark、当日を openapi から取り、turnmark が404の過去日は openapi で代わりに取る |
| `--date` / `--from` `--to` | 当日(JST) | 対象日。範囲は両端を含む |
| `--sleep` | `1.5` | リクエスト間の待機秒数。**1.0未満を指定したらエラーにする** |
| `--force` | off | 既に取得済みのものも取り直す |
| `--dry-run` | off | 取得予定のURLを表示するだけで、アクセスはしない |
| `--max-requests` | `400` | 1回の実行で送るリクエストの上限(暴走を防ぐ)。超えそうなら開始前にエラーにする |

終了コード: 0=正常(404が含まれても正常)、1=リトライしても失敗した日がある、2=引数エラー。

## 4. スキップ判定

ある日・あるソースについて、次の場合は取得しない。

1. 対象日が未来(当日より後) → 取得しない(404になるとわかっているため)
2. ファイルがあり、ログの最新記録が `final: true` → スキップ
3. 当日分の openapi で、最後に取得してから10分たっていない → スキップ(API側の更新間隔が約10分のため)
4. ログの最新記録が404の過去日 → スキップ(`--force` で再試行)。ただし turnmark の前日分が404だった場合は、公開待ちの可能性があるので次回も試す

## 5. 待機・リトライ

- **リトライや404も含め、すべてのリクエストの間で `--sleep` 秒以上空ける**。連続で走らせても間隔が守られるよう、前回のリクエスト時刻はログから読む。
- タイムアウトは30秒。User-Agent は `boatrace-yoso/0.1 (personal use)`(既存のものを引き継ぐ)。
- リトライ対象: タイムアウト、接続エラー、HTTP 5xx、429。
  - 待ち時間は 2秒→4秒→8秒の指数バックオフで、最大3回。429で `Retry-After` があればその値(上限300秒)に従う。
- リトライしない: 404(データなし)、その他の4xx。
- 異なる日付で3回続けて失敗したら、それ以降を取得せずに終了する(相手側の障害時に叩き続けない)。
- JSONとして読めないレスポンスは失敗扱いにし、保存しない。

## 6. 取得後の簡易チェック(収集側の責務)

保存の前に次を確認し、異常はログに `warning` として残す(保存はする)。

- トップレベルが `{"programs": {"stadiums": {...}}}` になっていること
- 開催場数が1〜24であること
- turnmark: `odds` キーがあるレースの割合が5割未満なら warning

詳しい検証は特徴量担当の interim 変換で行う。

## 7. テスト(tests/collect/)

- ネットワークを使わないこと(`urllib` をモックする)。
- スキップ判定・リトライ回数・待機間隔(待機関数を差し替えて呼び出し回数と秒数を確認)・`--sleep < 1.0` のエラーを確認する。

## 8. 完了条件

- `--source turnmark --from 2026-01-01 --to 2026-11-01` が1回で最後まで通り、もう一度実行すると0リクエストで終わる。
- `--dry-run` でURL一覧が出る。
- `load_raw("turnmark", date(2026,11,1))` で dict が返る。
- `data/raw/` 以下が git status に出ない(`.gitignore` は統合担当が管理する)。
