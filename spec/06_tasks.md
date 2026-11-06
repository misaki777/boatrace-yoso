# 06 タスク分解・担当・ファイル境界

## 1. 担当(ロール)

| ID | ロール | 主な責任 |
|---|---|---|
| P | PM / 統合担当 | 仕様の管理、共通ファイル、依存ライブラリ、担当をまたぐ調整、レビュー |
| C | 収集担当 | 外部APIの取得、生データの保存と読み込み |
| F | 特徴量担当 | interimテーブル、特徴量、リーク防止 |
| M | モデル担当 | 学習、評価、バックテスト、当日の予想、組合せ確率 |
| E | エクスポート担当 | 公開JSONの生成、結果の反映、成績の集計 |
| W | PWAフロント担当 | docs/ のアプリ本体 |
| R | 中継担当(2026-11-03 追加) | 締切前オッズを取ってスマホに返す Cloudflare Worker(`07` 9章) |
| DA | デザイナーA | 情報設計、画面デザイン |
| DB | デザイナーB | ビジュアル、デザイントークン、部品、アイコン |

## 2. ファイル境界(編集してよい範囲)

**原則: 各担当は自分の範囲のファイルだけを作成・編集する。他の担当の範囲は読む(import する)だけ。** 他の範囲に変更が必要になったら、その担当に依頼する(またはPMに相談する)。

| 担当 | 編集してよいパス | 読むだけのパス |
|---|---|---|
| P | `spec/**`、`.gitignore`、`requirements.txt`、`README.md`、`src/__init__.py`、`src/common/**`、`tests/common/**`、`scripts/**`(Phase 2 の統合スクリプトから)、(Phase 3)`.github/**` | 全体 |
| C | `src/collect/**`、`tests/collect/**`、`data/raw/**`、`tests/fixtures/official_odds/**`(Python と JS で共通の合成HTMLと期待値) | `src/common/**` |
| R | `workers/odds-proxy/**`、`workers/beforeinfo/**` | `spec/**`、`tests/fixtures/official_odds/**`、`src/collect/official_odds.py`(解析の参考) |
| F | `src/features/**`、`tests/features/**`、`data/interim/**`、`data/features/**` | `src/common/**`、`src/collect/raw_io.py`、`data/raw/**` |
| M | `src/model/**`、`tests/model/**`、`models/**`、`data/predictions/**`、`reports/**` | `src/common/**`、`src/features/feature_sets.py`、`data/interim/**`、`data/features/**` |
| E | `src/export/**`、`tests/export/**`、`docs/data/**`、`docs/data-local/**`(git管理外のローカルJSON) | `src/common/**`、`src/model/combinations.py`、`data/interim/**`、`data/predictions/**`、`reports/**`、`models/latest` |
| W | (Phase 3a: 端末内データの IndexedDB も W の責任範囲)`docs/**` のうち `docs/data/**` と `docs/data-local/**` を**除く**全部(`docs/index.html`、`docs/app/**`、`docs/sw.js`、`docs/manifest.webmanifest`、`docs/icons/**`)、フロントのテスト `docs/app/tests/**` | `docs/data/**`、`docs/data-local/**`、`design/**` |
| DA | `design/screens/**` | `spec/**`、`docs/data/sample/**`、`design/visual/**` |
| DB | `design/visual/**` | `spec/**`、`design/screens/**` |

- `CLAUDE.md` は利用者本人が管理する。どの担当も編集しない。
- 担当の間の契約(インターフェース)は次の4つ。変えるときはPMを通す。
  1. `src/collect/raw_io.py` の `load_raw()`(C→F)
  2. interim の parquet の列(`02_features.md` 1章)と `src/features/feature_sets.py`(F→M)
  3. `data/predictions/*.parquet` の列と `src/model/combinations.py` の関数(M→E)
  4. `docs/data/` の JSON スキーマ(`04_export_json.md`、`07` 3章)(E→W・デザイナー)
  5. `src/collect/raw_io.py` の `load_official_odds()` と締切前オッズのJSONの形(`07` 2.6)(C→E・M)
  6. 推奨買い目の計算ルールの共通テストケース `docs/data/sample/strategy_cases.json`(`07` 4.1、9.6)(E→W)
  7. 中継 Worker の API(`07` 9.2)(R→W)と、解析の共通テストデータ `tests/fixtures/official_odds/`(`07` 9.5)(C→R)
- git の commit と push は、利用者の指示があるまで誰も行わない。

## 3. タスク一覧

凡例: 依存=先に終わっている必要があるタスク。並行=依存が無く、すぐに始められるもの。

### Phase 0 / 第1波(すべて並行で開始できる)

| ID | 担当 | 内容 | 入力 | 出力 | 依存 | 完了条件 |
|---|---|---|---|---|---|---|
| P-0 | P | `.gitignore` に `data/interim/`、`data/features/`、`data/predictions/`、`models/`、`reports/` を追加。`requirements.txt` を作る(7章)。`src/common/codes.py`(場名、場→都道府県、グレード、級別、支部名、決まり手、風向の分類、着順コードの判定)と、そのテスト | `spec/00`、APIのcodes.md | 左記のファイル | .venv の調査結果(別担当) | `pip install -r requirements.txt` が通る。codes のテストが通る |
| C-1 | C | `src/collect/fetch.py`(期間指定、スキップ、リトライ、待機、取得ログ)と `raw_io.py`。既存の `data/raw/20261102.json` を新しい置き場所へ移す。`fetch_openapi.py` を整理する | `spec/01` | `src/collect/*`、`tests/collect/*` | なし | `01_collect.md` 8章 |
| M-1 | M | `src/model/combinations.py`: p_win → 各券種の確率(Harville。γ補正の引数あり、既定は1)、欠場艇を除いた再正規化、必要オッズ | `spec/03` 2章 | 同ファイルとテスト | なし | `03_model.md` 8章の combinations の条件 |
| E-1 | E | `src/export/schema.py`(構造の検証)と `make_sample.py`。`docs/data/sample/` に架空データのサンプル(状態をひととおり含める: 締切前・結果あり・中止・予想なし・見送り・欠場あり) | `spec/04` | `docs/data/sample/**` | なし | サンプルが schema.py の検証を通る |
| DA-1 | DA | S1〜S3 のワイヤーフレーム(低精度でよい)。情報の優先度の確認 | `spec/05`、`spec/04` | `design/screens/wireframes.*` | なし | PMのレビューを通る |
| DB-1 | DB | デザイントークン案(ライト・ダーク)、枠番の色、確率バー・的中バッジ・必要オッズ表示の部品案 | `spec/05` | `design/visual/tokens.json`、部品の資料 | なし | PMのレビューを通る |

### Phase 1 (MVP)

| ID | 担当 | 内容 | 依存 | 並行できるもの | 完了条件 |
|---|---|---|---|---|---|
| C-2 | C | 過去分の一括取得: turnmark 2026-01-01〜前日(約305リクエスト。`--sleep 1.5`) | C-1 | 他の全タスク | 取得ログにすべての日が200か404で記録されている |
| F-1 | F | `build_interim`(races / entries / payouts / odds)と品質レポート。開発中は `tests/features/fixtures` の小さなデータを使う | C-1(raw_ioのI/F)。実データでの実行は C-2 の後 | M-1、E-1、W系 | `02_features.md` 6章 |
| F-2 | F | `build_features --variant pre`(P・H・R 特徴量)、`feature_sets.py`、リークのテスト | F-1 | M-2 の準備 | 同上 |
| F-3 | F | 当日用の特徴量(`--date`。目的変数なし) | F-2 | M-3 | 学習用と同じ関数を通っている |
| M-2 | M | ベースライン B0・B1、`evaluate`(指標一式) | F-1(B0 は interim だけで作れる)、M-1 | F-2 | valid/test の指標が出る |
| M-3 | M | LightGBM(pre)の学習、温度Tの調整、`meta.json`、`models/latest` | F-2、M-2 | E-2 の準備 | B1 より logloss が良い(良くなければ報告して相談) |
| M-4 | M | `backtest`(戦略 S1〜S5、ブートストラップ区間)、`reports/model_comparison.md` | M-3 | W系 | `03_model.md` 8章 |
| M-5 | M | `predict --date`(当日の予想の parquet) | M-3、F-3 | | 全レースで p_win の合計が1 |
| E-2 | E | `export_day`: 予想 → `days/YYYYMMDD.json`(picks、recommended、必要オッズ) | E-1、M-1、M-5(列の仕様だけで開発を始められる) | W系 | 実データで schema の検証を通る |
| E-3 | E | 結果の反映(openapi の払戻)、`index.json`、`performance.json`(live と backtest) | E-2、M-4 | | 2日分の実データで正しく集計できる(手計算で1レース分を照合する) |
| E-4 | E | 翌日の確定オッズの反映(`final_odds`。推奨買い目の分だけ)、`status: final` | E-3、F-1(odds) | | 公開してはいけない項目のテストが通る |
| W-1 | W | アプリの骨組み: ルーティング(ハッシュ)、JSONの読み込み、`?data=sample`、エラー表示、JSTでの時刻表示 | E-1 | DA-1、DB-1 | サンプルデータで画面の切り替えができる |
| W-2 | W | S1 ホーム、S4 日付の選択 | W-1、DA-1 | W-3 | `05_pwa.md` 3・6章の優先度Aがすべて表示される |
| W-3 | W | S2 レース詳細 | W-1、DA-1 | W-2 | `05_pwa.md` 4章の優先度Aがすべて表示される |
| W-4 | W | S3 成績、S5 このアプリについて | W-1 | W-2、W-3 | 同 5・7章 |
| W-5 | W | manifest、Service Worker(キャッシュ方針)、アイコンの取り込み、サブパス対応 | W-1、DB-2 | | オフラインでも前回のデータが表示される。ホーム画面に追加できる |
| W-6 | W | デザインの反映(トークン、部品) | DA-2、DB-1 | | デザイナーの確認を通る |
| DA-2 | DA | S1〜S5 の画面デザイン(状態ごと、360/430px) | DA-1 | DB-1 | PMのレビューを通る |
| DB-2 | DB | アプリアイコン(192/512、maskable)。iOS のスプラッシュは MVP では作らない(2026-11-03 決定) | DB-1 | | `design/visual/icons/` に置かれている |
| F-1a | F | (2026-11-03 追加・**完了**)races に `is_canceled`(NA可の3値)、`n_entries`、`has_dead_heat` 列を追加する(`02_features.md` 1.1) | F-1 | | export_day がこの列を使って中止レースを判定できる |
| M-3a | M | (2026-11-03 追加)`meta.json` に `train_until`(= `periods.train_to`)と `harville_gamma`(使わないときは null)を必ず出す(`03_model.md` 3章) | M-3 | | export が meta.json だけで γ と学習期間を取得できる |
| P-1 | P | 結合テスト: 当日分を手動で一通り流す手順を `README.md` に書き、実際に通す | E-3、W-2〜W-5 | | 手順どおりにスマホで今日の予想が見られる |

### Phase 2

| ID | 担当 | 内容 | 依存 |
|---|---|---|---|
| C-4 | C | 当日の openapi を10分以上の間隔で再取得するモード(`--watch` か、外から何度も呼ぶ前提で問題ないか確認する) | C-1 |
| F-4 | F | `--variant preview` の特徴量(V 区分)。**Phase 1 中に先行して作成済み(2026-11-03)**。M-6 はこれを使って始めてよい | F-2 |
| M-6 | M | 直前モデル、Harvilleのγ補正、lambdarank との比較、較正の図 | M-4、F-4 |
| E-5 | E | 直前予想での `prediction` の更新(variant=preview、欠場艇を除いた再計算) | E-2、M-6 |
| W-7 | W | S7 オッズ入力→期待値、S1 の絞り込み(S6 は下の W-8 に移した) | W-3 |
| DA-3 | DA | S6・S7 の画面(**内容は下の「締切前オッズと買い目設定」の DA-3 に置き換え**) | DA-2 |

### Phase 2: 締切前オッズと買い目設定(2026-11-03 追加。仕様は `07_official_odds_and_settings.md`)

タスク番号は、既にある E-5・E-6・W-7 と重ならないように E-7〜E-9、W-8〜W-9 とした。

| ID | 担当 | 内容 | 入力 → 出力 | 依存 | 並行 | 完了条件 |
|---|---|---|---|---|---|---|
| C-5 | C | (**完了 2026-11-03**。一括取得モード `--window-minutes`、`_targets.json`(not_targeted)を含む。未確認: 実サイトでの欠場艇の表示、`odds3f`・`oddsk` の解析(今は保存だけ)、ループ・一括取得の長時間の動作)締切前オッズの取得: `src/collect/official_odds.py`(1レース取得・常駐ループ・dry-run)、`html.parser` による解析、停止条件、取得ログ、`raw_io.load_official_odds()` | 公式サイト・openapiの当日分 → `data/raw/official_odds/**` | C-1 | E-8(JSONの形だけで始められる)、DA-3、DB-3 | (1) 実際の当日のレースで1回取り、`tab4_time` の締切前の表記を確認して 07 2.1 に追記を依頼する (2) 解析のテストが、保存したHTMLで通る(3連単は `2-1-6`=27.5 の検算を含む) (3) `--dry-run` で1日のリクエスト数と予定時刻が出る (4) 403/429・連続失敗で止まることをテストで確認する |
| P-2 | P | (**2026-11-03 実装済み。長時間の実行による結合確認は C-5・E-8 の完了後**。詳細は 07 2.8)統合スクリプト `scripts/live_loop.py`(オッズの取得 → export の更新を順に呼ぶ)、`.gitignore` に `docs/data-local/` と `data/raw/official_odds/` の確認、README にローカル表示(LAN、WSL のポート転送)の手順 | — | C-5、E-8 | | PC1台でループを1時間動かし、スマホのローカル表示で期待値のランクが更新されるのを確認する |
| E-7 | E | `final_odds` の公開範囲の修正(公開JSONは `ev_rank` だけ、数値はローカルJSONへ)。`PUBLISH_ODDS_NUMERIC`(既定 False)の設定 | — | E-4 | C-5 | 公開してはいけない項目のテストに「`docs/data/` に `odds`・`ev` の数値が無い」を加えて通る |
| E-8 | E | 締切前オッズによる期待値: `odds_snapshot`、picks・recommended の `ev_rank`、ローカルJSON `docs/data-local/` の出力、`export_day --odds-only`(オッズだけ反映する速い更新) | `load_official_odds()`・予想 → `docs/data/`、`docs/data-local/` | E-2、M-1、C-5(関数の仕様だけで開発を始められる) | C-5、W-8 | サンプルにも `odds_snapshot`・`ev_rank` の例(ok・late・未取得)を入れ、schema の検証を通る |
| E-9 | E | 推奨買い目の計算ルールのPython実装と、共通のテストケース `docs/data/sample/strategy_cases.json`(07 4.2 の設定の組合せ20件以上) | — | E-2 | W-8 | 既定の設定で、export の `recommended` とケースの答えが一致する |
| M-7 | M | オッズの変動の分析(締切前 vs 確定)と、S4 の割引率の実測値への置き換え | `data/raw/official_odds/`・interim の odds → `reports/odds_drift/` | C-5 の取得が1〜2週間分貯まってから | | 券種別・人気順別の変化率の分布が出る |
| M-8 | M | フォワードテスト(締切前オッズで選び、確定の払戻で評価)と較正の月次確認 | → `reports/forward/` | C-5、M-4 | M-7 | 日次で追記され、1か月分で U2 の判断材料になる |
| DA-3 | DA | S6 設定画面(07 4.2 の項目と適用順の見せ方)、S2 の期待値ランク・オッズ取得時刻・注意書きの表示、S3 の「既定の買い方で集計」の注記、ローカル表示の目印 | `07`、`04` → `design/screens/` | DA-2 | DB-3、C-5、E-8 | PMのレビューを通る |
| DB-3 | DB | 部品: 期待値ランク(A〜D と「オッズなし」)のバッジ、オッズの時点の表示、設定画面の部品(複数選択・段階の選択)、「ローカル表示」の目印 | → `design/visual/` | DB-1 | DA-3 | PMのレビューを通る |
| W-8 | W | S6 設定(localStorage `settings.v1`)と、picks からの推奨買い目の計算し直し。`strategy_cases.json` でのテスト | `docs/data/**` → `docs/app/**` | W-3、E-9(ケースが無い間は 07 4.2 の規則で実装を始めてよい) | E-8 | 共通ケースがすべて通る。既定の設定で export の `recommended` と一致する |
| W-9 | W | S2 の期待値ランクの表示、オッズの取得時刻と注意書き、S3 の注記、`?data=local` とローカル表示の切り替え | → `docs/app/**` | W-3、E-8、DA-3 | W-8 | ローカルJSONとサンプルの両方で表示できる。LANのhttpで Service Worker が無くても動く |

依存の流れ:

```
C-5 ──→ E-8 ──→ W-9
  │       └──→ P-2(ループの結合)
  └──(1〜2週間分貯まる)→ M-7、M-8
E-9 ──→ W-8
E-4 ──→ E-7
DA-3 + DB-3 ──→ W-8・W-9 の見た目の反映
```

すぐに並行で始められるもの: C-5、E-7、E-9、DA-3、DB-3(W-8 も 07 4.2 の規則で始められる)。

### Phase 2: 中継 Worker(2026-11-03 追加。仕様は `07` 9章)

| ID | 担当 | 内容 | 入力 → 出力 | 依存 | 並行 | 完了条件 |
|---|---|---|---|---|---|---|
| C-6 | C | (**完了 2026-11-03**)共通の合成HTMLと期待値 `tests/fixtures/official_odds/`(3ページ分、欠場の空欄、締切前・締切時の表記、壊れた例)。Python 側のテストをこのデータに切り替える。**実物のHTMLをリポジトリに置かない** | 実物のHTML(ローカル) → 合成HTML・`*.expected.json` | C-5 | R-1(R はデータ待ちの間、9.4 の方法で実装を始めてよい) | Python の解析が全ファイルで期待値と一致し、壊れた例で失敗する。合成HTMLに選手名・実際の数値が入っていない |
| R-1 | R | Worker 本体: `src/index.js`(認証・CORS・入力チェック・締切判定・KVキャッシュ60秒・1日の上限・停止フラグ・順番の取得と1秒の間隔)、`src/parse.js`、`src/core.js`、`wrangler.toml.example` | `07` 9.2〜9.4、9.8、9.9 → `workers/odds-proxy/**` | なし(C-6 の前は自作の仮データで) | C-6、E-10、W-10 | `node --test workers/odds-proxy/test/` が通る。テストに次を含む: 合言葉違いは401、許可していないオリジンは403、締切後は409、上限で429、停止中は503、キャッシュ中は公式に取りに行かない、ページの間を1秒以上空ける、ログにオッズの数値が出ない |
| R-2 | R | 解析の一致: `tests/fixtures/official_odds/` を読むテストで、Python と同じ結果になることを確かめる。CPU時間の計測(解析1ページの所要時間) | C-6 の共通データ | R-1、C-6 | | 全ファイルで期待値と一致。解析の所要時間を README に記録する(目安: 1ページ数ms以内) |
| R-3 | R | `workers/odds-proxy/README.md`: デプロイ手順(アカウント作成、KV の作成、`wrangler secret put ODDS_KEY`、`ALLOWED_ORIGINS`、`DAILY_LIMIT`)、デプロイ後の確認手順(公式サイトに接続できるか、1レース取得して時間を測る)、無料枠の使用量の確認方法、止め方 | | R-1 | | 利用者が手順どおりにデプロイできる(実際のデプロイは利用者が後で行う) |
| E-10 | E | `strategy_cases.json` に、数値のオッズを入力に含むケース(期待値で絞る設定、一部の券種のオッズが無い場合)を追加 | | E-9 | R-1、W-10 | Python の計算がすべてのケースで期待値と一致する |
| W-10 | W | レース詳細のオッズ取得(ボタン・自動取得の設定・取得中・取得時刻・更新・各状態の表示)、Worker の URL と合言葉の設定、取得したオッズでの期待値と推奨買い目の再計算(`07` 9.6・9.7) | Worker の API → `docs/app/**` | W-8、E-10(ケース)。Worker の本物は不要(偽の応答で開発する) | R-1 | `strategy_cases.json` のオッズ入りのケースがすべて通る。取得したオッズを localStorage・外部に保存・送信していない。各状態を偽の応答で確認できる |
| DA-4 | DA | S2 のオッズ取得の操作と状態(未設定・取得中・成功・一部失敗・失敗・上限・締切後・停止中・オフライン)、S6 の Worker 設定(URL・合言葉・自動取得) | `07` 9.7 → `design/screens/` | DA-3 | DB-4 | PMのレビューを通る |
| DB-4 | DB | 部品: 取得ボタンの状態(押せる・取得中・待ち時間)、取得中の表示、取得時刻とキャッシュの表示、エラーの帯、合言葉の入力欄 | → `design/visual/` | DB-3 | DA-4 | PMのレビューを通る |
| P-3 | P(利用者) | Cloudflare のアカウント作成とデプロイ(R-3 の手順で)。`ALLOWED_ORIGINS` に自宅LANのオリジンを入れるか決める(`07` Q6) | | R-3 | | 実機のスマホからオッズを1回取得できる |

依存の流れ:

```
C-5 → C-6 ─┐
R-1 ───────┴→ R-2 → R-3 → P-3(利用者がデプロイ)
E-9 → E-10 ─→ W-10 ←─ W-8
DA-3 → DA-4、DB-3 → DB-4 ─→ W-10 の見た目
```

すぐに並行で始められるもの: R-1、C-6(C-5 の解析ができていれば)、E-10、DA-4、DB-4。W-10 は W-8 の後。

### Phase 3a: ペルソナ由来の機能(2026-11-04 追加。仕様は `08_personas_and_features.md` 5章、`04` 8章、`05` 11章)

| ID | 担当 | 内容 | 依存 | 並行 | 完了条件 |
|---|---|---|---|---|---|
| M-9 | M | **予想の確定**: predict は確定版があれば書かずに終わる。`--force` で上書きし、旧版を `data/predictions/superseded/` に退避。`locked_at` を持たせる(08 5.1) | M-5 | 他の全タスク | 同じ日に2回実行しても確定版が変わらないテストが通る |
| M-10 | M | **根拠トップ3**: `pred_contrib` から上位3の特徴量を `YYYYMMDD_{variant}_contrib.parquet` に出す。表示ラベルの対応表 `src/model/feature_labels.py`(08 5.2) | M-5 | M-9、M-11 | 全艇に3件(特徴量が足りなければそれ以下)。ラベルの無い特徴量の一覧を報告する |
| M-11 | M | **較正と戦略比較の集計関数**(Wilson区間、ブートストラップ区間、最大連敗、最大ドローダウン、基準戦略2つ)と、backtest の `reports/` への出力。E が import できる関数にする(08 5.3) | M-4 | M-9、M-10 | test 期間で `04` 8.2 の形の値が出る |
| E-11 | E | **確定版からの出力**: `prediction` は確定版から作り、過去日の予想を作り直さない。`locked_at`・`version`・`model_id`、`live` の集計を確定版だけから(版ごと、モデルの期間) | M-9 | E-12 | export を何回実行しても `prediction` が変わらないテスト |
| E-12 | E | `reasons`(段階化)、`skip_reason`、`preview_info` の出力と、`performance.json` の較正・戦略比較・月別・`sample_note`(M-11 の関数を使う) | M-10、M-11、E-11 | W-11〜W-13(サンプルを先に) | **サンプル(`docs/data/sample/`)を最初に更新**し、schema の検証と「公開してはいけない項目」のテスト(寄与の生の数値・購入記録が無い)が通る |
| DA-5 | DA | S1 の場で絞る表示、S2 の追加ブロック(版・根拠・印・メモ・買い目合計・直前情報・見送り理由)、S3 の較正グラフ・戦略比較・タブ、**S8 収支・S9 自分の予想の成績**、用語説明、依存対策の表示 | なし(08・04 8章の項目で) | DB-5 | PMのレビュー(08 4章の表現のルールを満たす) |
| DB-5 | DB | 部品: 較正グラフ、区間付きの数値、件数が少ないときの薄い表示、印の入力、根拠の上げ下げ表示、買い目合計のパネル、警告の帯、「?」の吹き出し | なし | DA-5 | PMのレビュー |
| W-11 | W | **端末内データ**(IndexedDB: 購入記録・印・メモ・予算、バックアップの書き出し・読み込み)と **S8 収支**、予算・連敗の警告、**S9** | W-8 | E-12、DA-5 | 払戻が結果から自動で計算される。テスト用のデータで収支・最大ドローダウンが手計算と一致する。外部に送信しない |
| W-12 | W | S2 の追加(版・根拠・印・メモ・買い目合計とコピー・購入を記録・公式トップへのリンク・直前情報・見送り理由)、S1 の場で絞る表示 | E-12 のサンプル、W-11 | W-13 | 合成オッズとトリガミ回避の配分のテストが通る。コピーにオッズの数値が入らない |
| W-13 | W | S3 の較正グラフ・戦略比較・月別・検証/実運用・版の切り替え、用語説明「?」、S5 の相談窓口、深夜・連続利用の表示 | E-12 のサンプル | W-12 | 200R未満が薄く表示される。過去日のファイル(項目なし)でも壊れない |
| P-4 | P | (**完了 2026-11-04: `spec/09_wording.md`**)表現の文言(用語説明、注意書き、相談窓口の記載)を用意し、DA・W に渡す。各画面の最終確認 | — | DA-5 | 文言が 08 4章のルールを満たす |

並行: M-9・M-10・M-11、DA-5・DB-5、P-4 はすぐに開始できる。E-11 は M-9 の後、E-12 はサンプルの更新を先に行えば W-12・W-13 と並行できる。W-11 は E-12 を待たずに開始できる(端末内で完結するため)。
保留: 直前版の予想(F13)は M の直前モデル検証の結果を見て PM が判断する。

### 特徴量 v2(2026-11-05 追加。仕様は `02` 7章)

| ID | 担当 | 内容 | 依存 | 状態・完了条件 |
|---|---|---|---|---|
| F-6 | F | レース運びの特徴量(`src/features/style.py`、`STYLE_H_FEATURES` 28列・`STYLE_R_FEATURES` 4列)と variant `pre_v2` / `preview_v2` | F-2 | **完了(2026-11-05)** |
| M-12 | M | v2 の学習と比較(`pre` vs `pre_v2`、`preview` vs `preview_v2`。valid/test の logloss・較正・回収率と区間)。採用する場合は `models/latest` の切り替えを PM に相談(確定版の予想は書き換えない。`08` 5.1) | F-6、M-11 | **進行中**。比較表を `reports/` に出し、採用の判断材料を報告する |

### Phase 3b(2026-11-05 追加)

仕様・タスク一覧・順番は `10_phase3b.md` 5章を参照(CI-1〜3、W-14〜17、C-7、F-7〜10、M-13・M-14、E-13・E-14、DA-6・DB-6、P-5)。CI の担当範囲は `.github/**` と `scripts/ci_*`・`scripts/deploy_pages.sh`。確認事項は U14(`10` 6章 Q13・Q14)。

### データ分析(2026-11-05 追加)

仕様・タスク・順番は `11_analytics.md` 4章(P-6、F-11〜13、M-15〜17、E-15、CI-4、DA-7・DB-7、W-18〜21)。確認事項は U15(`11` 5章 Q15・Q16)。

### 直前情報を時間を決めて取る(2026-11-06 追加)

仕様・タスク・順番は `12_beforeinfo.md` 4章(C-8、R-4、CI-5(**完了**)、F-12b、M-18、P-7)。中継担当(R)の範囲に `workers/beforeinfo/**`、収集担当(C)の範囲に `tests/fixtures/beforeinfo/**` を加える。確認事項は U16(`12` 5章 Q17・Q18)。

### Phase 3〜4(概要のみ。着手前に仕様を詳しくする)

- P-3: GitHub Actions(収集→予想→export→docs/data の commit)。実行の頻度と、外部へのアクセス回数の上限を決める
- E-6: 古い `days/` の整理
- C-3 / F-5: 旧API(2016年〜)の取り込み(約1.1万リクエスト。1.5秒間隔で約5時間。夜間に分けて実行する)と、スキーマの変換

## 4. 並行作業の流れ(クリティカルパス)

```
第1波(並行): P-0  C-1  M-1  E-1  DA-1  DB-1
                │    │          │     │     │
                │   C-2(取得、約8分)  W-1    DA-2  DB-2
                │    │          │     │
                └──→ F-1 ─→ F-2 ─→ M-2 ─→ M-3 ─→ M-4
                                  └─→ F-3 ─→ M-5 ─→ E-2 ─→ E-3 ─→ E-4
                                                   W-2/W-3/W-4/W-5 ─→ W-6
                                                                     └→ P-1(結合)
```

クリティカルパス: C-1 → C-2 → F-1 → F-2 → M-3 → M-5 → E-2 → E-3 → P-1。
フロントとデザインは、サンプルJSON(E-1)さえあれば、データ処理側の進み具合を待たずに進められる。

## 5. 作業の決まり(全担当)

- コメントと説明は日本語、変数名・関数名は英語(CLAUDE.md)。
- 外部へのアクセスは収集担当(C)のコードを通じてだけ行う。他の担当がデータを取得するコードを書かない。
- テストはネットワークを使わずに実行できるようにする。テストの実行は `python -m pytest tests/<自分の範囲>`。
- 新しいライブラリが必要になったら、PMに相談する(`requirements.txt` は P だけが編集する)。
- 大きな中間ファイル(parquet、モデル)はコミットしない(`.gitignore` で除外する)。

## 6. 未決事項(PM・利用者の判断が必要なもの)

| # | 事項 | 現在の案 | 期限の目安 |
|---|---|---|---|
| U1 | 公式サイトからオッズを取得するか | **決定(2026-11-03、利用者)**: 取得する。規約の確認と設計は `07_official_odds_and_settings.md`。ローカルからだけ取り、数値は公開しない。残っている確認事項は 07 8章の Q1〜Q5 | 決定済み |
| U2 | 推奨買い目の既定の戦略 | 3連単の上位5点(S1 `top_k`, k=5)を仮の既定とし、M-4 のバックテスト結果を見て決める。サンプルJSONだけは、見送りの表示を確かめるため `confidence_skip`(k=5、alpha=0.35)にしている(2026-11-03 承認) | M-4 の完了時 |
| U3 | 選手名を公開JSONに含めるか | **決定(2026-11-03、利用者)**: 含める(現状どおり) | 決定済み |
| U4 | GitHub Pagesの公開範囲と検索エンジン対策 | `docs/` を main ブランチから公開する。`<meta name="robots" content="noindex">` を付ける。**公開・commit・push は利用者の判断で後回し(2026-11-03)**。当面はローカル表示(07 5章) | 公開を始める前 |
| U5 | docs/data の毎日のコミットによるリポジトリの肥大化 | 実測で約0.7MB/日(1日のファイルを何度も更新すると、その分さらに増える) → 年に約250MB以上。Phase 3 で、古い days の削除か、別ブランチ(gh-pages)への出力を検討する | Phase 3 の着手前 |
| U6 | 同じ日の前のレースの結果を特徴量に使うか(同じ節・同じ日の調子) | MVP では使わない。Phase 2 以降に、効果とリークのリスクを評価してから決める | Phase 2 |
| U7 | 旧API(2016年〜)を取り込むか | 10か月分の学習で性能が頭打ちになったら検討する | Phase 4 |
| U8 | openapi v1 の対象期間 | 公式表記は2026-01-01以降だが、実測では2025-12-31にもデータがある(2025-01-01は404)。2025-12-31 は使わない(turnmark と期間をそろえる) | 決定済み(変更があれば見直す) |
| U9 | Python 3.14 で LightGBM・pyarrow が入るか | **解決(2026-11-03)**: .venv(Python 3.14.4)に pandas 3.1.0 / numpy 2.5.4 / lightgbm 4.7.0 / scikit-learn 1.9.1 / pyarrow 26.0.0 / pytest 9.2.0 を導入済み。OS の `libgomp1` が無い問題は、`sudo apt install libgomp1` を利用者の判断で後回しにしたため、**`.venv/lib/extra/` に libgomp をコピーし、`.venv/bin/activate` が `LD_LIBRARY_PATH` に `.venv/lib/extra` を足す**形で回避した(セッションが終わっても `source .venv/bin/activate` すれば動く)。**全担当: LightGBM を使うコマンドは activate してから実行する**(`.venv/bin/python` を直接使うと import に失敗する)。`scripts/live_loop.py` は子プロセスにこのパスを自動で渡す。注意: `.venv` を作り直すとこの回避策は消える(README の手順で再設定するか、`sudo apt install libgomp1` を行う) | libgomp1 の正式な導入は、利用者の都合のよいとき |
| U11 | アプリ名(manifest の `name` / `short_name`) | **利用者が決める**。決まるまでは仮の名前「ボートレース予想」/「BR予想」(`design/visual/README.md`)を使う | W-5 の完了前 |
| U12 | 中継 Worker 関連(`07` 9.11 の Q6〜Q9: LANのオリジンの許可、Node 18 以上の導入、1日の上限とキャッシュ時間、Cloudflare から公式に接続できない場合) | 07 9.11 の案のとおり | R-1 の完了まで |
| U13 | Phase 3a の確認事項(`08` 7章 Q10〜Q12: 購入記録は端末内のみ、予算・連敗の警告の既定、予想の確定の既定と過去日の扱い) | 08 7章の案 | E-11・W-11 の着手前 |
| U14 | Phase 3b の確認事項(`10` 6章: 旧APIの取得を PC で3晩、毎月のモデルの自動切り替え) | 10 6章の案 | C-7・M-13 の着手前 |
| U15 | データ分析の確認事項(`11` 5章) | **決定(2026-11-05)**: 週1ジョブは月曜 3:00 JST(cron `0 18 * * 0`。毎時0分は GitHub の定期実行が混み、数分〜数十分遅れることがある)。シミュレーターの最大損失は1レース1,000円の想定(実際の予算・収支は使わない) | 決定済み |
| U16 | 直前情報の取得の確認事項(`12` 5章) | **決定(2026-11-06)**: GitHub の鍵は dispatcher と同じものを使う。取得は締切15〜10分前に1回だけ(取り直しなし)、1日 約150〜300ページ、上限300。スマホでの再予想は定刻取得の稼働後のおまけ(`12` 6章) | 決定済み |
| U10 | 取得元が使えなくなった場合(どちらも非公式・個人運営) | turnmark が止まったら openapi だけで動かす(オッズの評価ができなくなる)。両方止まった場合の代わりは未定 | 随時 |

## 7. 追加ライブラリの候補

導入は CLAUDE.md の方針どおり、相談してから行う。

| ライブラリ | 区分 | 理由 | 使う担当 |
|---|---|---|---|
| `pandas` | 必須 | interim・特徴量の表の操作、時系列の集計(rolling、groupby) | F、M、E |
| `numpy` | 必須(pandasの依存) | 確率計算(Harville、softmax)のベクトル演算 | M |
| `pyarrow` | 必須 | parquet の読み書き。3連単のオッズ(約550万行)を CSV で扱うのは遅く、容量も大きいため | F、M、E |
| `lightgbm` | 必須 | 主力のモデル。CPUで速く動き、カテゴリ変数と欠損値をそのまま扱える | M |
| `scikit-learn` | 推奨 | ベースラインB1(ロジスティック回帰)、log_loss などの指標、較正の確認 | M |
| `pytest` | 推奨(開発用) | 各担当のユニットテスト | 全員 |
| `matplotlib` | 任意(Phase 2) | 較正の図、回収率の推移の図(reports/ 用。PWAでは使わない) | M |

使わないもの:
- `requests`: 取得は標準ライブラリの `urllib` で十分(既存の実装もこれで書かれている)
- `jsonschema`: 公開JSONの検証は、標準ライブラリだけで小さく書く
- フロント: npm パッケージやビルドツールは使わない(05章)

`requirements.txt`(P-0 で確定。Python 3.14.4 で導入済み):

```
pandas==3.1.0
numpy==2.5.4
lightgbm==4.7.0
scikit-learn==1.9.1
pyarrow==26.0.0
pytest==9.2.0
```

LightGBM の実行には、OS 側の `libgomp1` が必要(U9)。matplotlib は Phase 2 で必要になった時点で相談する。

テストの実行: `.venv/bin/python -m pytest tests/<範囲>`(リポジトリ直下で実行する)。`tests/` 以下のテストファイル名は、担当の間で重複させないこと(例: `test_codes.py` は common が使用済み。`tests/<範囲>/` には `__init__.py` を置かないので、同じ名前のファイルがあると pytest が衝突する)。
