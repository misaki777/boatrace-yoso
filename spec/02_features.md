# 02 整形・特徴量仕様(src/features/)

担当: 特徴量担当。入力は `data/raw/`(`src/collect/raw_io.load_raw` で読む)、出力は `data/interim/` と `data/features/`。どちらもgit管理外。

ファイル形式は parquet(pyarrow)。列名は snake_case の英語。

---

## 1. interimテーブル(生JSON → 正規化)

CLI: `python -m src.features.build_interim --from 2026-01-01 --to 2026-11-01`

- 日付ごとに読むソースは「turnmark が final であれば turnmark、なければ openapi」。どちらを使ったかを `source` 列に残す。
- 出力は月ごとのパーティション(`data/interim/entries/2026-10.parquet` など)。再実行したときは、その月のファイルを丸ごと作り直す(冪等にする)。
- `_source` で終わる文字列フィールドは捨てる。ただし `day_number_source`(中止・順延の判定)と `subtitle` は残す。**例外: `pv_source`**(2026-11-06 追加。生データの表記の文字列ではなく、直前情報の出所を表す本ツールの列。1.5 参照)は `_source` で終わるが残す。

### 1.1 races(1行=1レース)

| 列 | 型 | 出典 | 備考 |
|---|---|---|---|
| race_id | str | 生成 | `YYYYMMDD-SS-RR` |
| date | date | programs | |
| stadium_number | int8 | | |
| race_number | int8 | | |
| closed_at | datetime(JST) | | |
| grade_number | int8 | | コード値 |
| title, subtitle | str | | |
| distance | int16 | | |
| day_number | Int8 | | 中止・順延のときはnull |
| day_status | str | day_number_source | `normal` / `canceled` / `postponed`。出走表がまるごと null のレース(タイトルも選手も無い)は `canceled` とする |
| n_entries | int8 | 生成 | 出走表に載っている艇の数(通常6) |
| has_dead_heat | bool | 生成 | 1〜6着に同じ着順の艇があるか(同着) |
| pv_wind_speed, pv_wind_direction_number, pv_wave_height, pv_weather_number, pv_air_temperature, pv_water_temperature | | preview | 直前の気象 |
| rs_wind_speed … rs_water_temperature | | result | **評価・分析専用。特徴量には使わない** |
| technique_number | Int8 | result | 決まり手 |
| is_valid_result | bool | 生成 | 学習・評価に使えるレースか。**結果がある(着順か失格・欠場の番号が1艇以上) かつ `n_entries == 6` かつ 1着がちょうど1艇** のとき true。順延の日でも、順延前に実施されて結果が出ているレースは true(実測で21件) |
| is_canceled | boolean(NA可) | 生成 | レースが行われなかったか(2026-11-03 追加。export が使う)。3値で持つ: 結果があれば **false**。結果が無く、`day_status` が `canceled`/`postponed` か、**確定済みのファイル**(`01_collect.md` の `final: true`)なら **true**。確定前の当日の openapi で結果がまだ無いレースは **NA**(結果待ちと中止を区別できないため)。export は NA を「まだ結果が無い」として扱い、中止にしない |
| has_refund | bool | result.refunds | 返還艇があるか |
| source | str | | `turnmark` / `openapi` |

### 1.2 entries(1行=1艇)

| 列 | 出典 | 備考 |
|---|---|---|
| race_id, date, stadium_number, race_number, entry_number | | キー |
| racer_number, racer_name | programs.racers | 選手の識別は登録番号で行う |
| rank_number, branch_number, birthplace_number, age, weight, flying_count, late_count, average_start_timing | programs.racers | |
| national_win_rate, national_top_2_percent, national_top_3_percent, local_win_rate, local_top_2_percent, local_top_3_percent | | |
| motor_number, motor_top_2_percent, motor_top_3_percent, boat_number, boat_top_2_percent, boat_top_3_percent | | |
| pv_course_number, pv_start_timing, pv_weight, pv_weight_adjustment, pv_exhibition_time, pv_tilt_adjustment, pv_propeller_new(bool), pv_parts_count | preview.racers | スタート展示の値。キーが無ければnull。`pv_parts_count` は **交換した部品の種類数**(`parts` 配列の要素数。個数 `quantity` は非公表で null のことが多いため合計しない)。直前情報がまだ無い艇では、`pv_propeller_new` と `pv_parts_count` も null |
| rs_course_number, rs_start_timing, place_number | result.racers | **目的変数・過去集計専用** |
| finish_position | 生成 | place_number が1〜6ならその値、それ以外はnull |
| is_absent | 生成 | place_number が14〜16(欠場系)、または返還艇 |
| is_disqualified | 生成 | place_number が7〜13 |

### 1.3 payouts(1行=1払戻要素)

列: `race_id, bet_type, combination, amount, label`。`amount` は100円あたりの円。`combination` が null(特払・不成立)の行も残す。

### 1.4 odds(1行=1組合せ。turnmarkのみ)

列: `race_id, bet_type, combination, odds, odds_lower, odds_upper`。turnmark のオッズは枠番の入れ子の dict(3連単なら `[1着][2着][3着]`)で届くので、これを1行=1組合せの縦長の表に展開する。`combination` の表記は `00_overview.md` 6章に合わせる(順序あり `1-2-3`、順不同 `1=2=3`)。
- 単勝・2連単・2連複・3連単・3連複は `odds` に値を入れ、拡連複・複勝は `odds_lower`/`odds_upper` に入れる。
- 3連単は1レース120行になる(10か月で約4.6万レースなので、約550万行)。月ごとのファイルに分け、型は float32 にする。
- **オッズは評価専用で、特徴量には使わない**(当日は手に入らないため)。

### 1.5 データ品質チェック(interimを作るとき)

- 1レースに6艇がそろっていること(足りないときは warning にして、そのレースを `is_valid_result=false` にする)
- 同じ着順が複数ある(同着)レースの数を記録する
- `finish_position` が1のものが、ちょうど1艇のレースを有効とする(同着1着は学習から外す)
- 結果は `data/interim/_quality/YYYY-MM.json` に件数として出す

---

## 2. 特徴量テーブル(1行=1艇)

CLI: `python -m src.features.build_features --variant pre|preview --from ... --to ...`
出力: `data/features/features_{variant}.parquet`(全期間で1ファイル。大きくなったら月ごとに分ける)

1行=1艇を基本の粒度とする。ただし同じレースの他の艇との比較(相対特徴)を列として持たせる。モデルは「レース内で6艇の確率を正規化する」形をとる(`03_model.md`)。

### 2.1 キー・目的変数・メタ列(特徴量ではない)

`race_id, date, stadium_number, race_number, entry_number, racer_number, finish_position, is_win(finish_position==1), is_top2, is_top3, is_valid_result, is_absent`

### 2.2 特徴量一覧

区分: **P** = 出走表(前日に確定)、**V** = 直前情報(variant=preview のときだけ)、**H** = 過去の結果の集計(対象日より前のデータだけで作る)、**R** = レース内の相対値

| # | 名前 | 区分 | 内容 |
|---|---|---|---|
| 1 | entry_number | P | 枠番(カテゴリ扱いでも数値扱いでも可。まず数値) |
| 2 | stadium_number | P | カテゴリ |
| 3 | race_number | P | |
| 4 | grade_number | P | カテゴリ |
| 5 | day_number | P | 開催何日目か |
| 6 | race_kind | P | subtitle をキーワードで分類したもの: `yosen`(予選) / `jun_yusho`(準優) / `yusho`(優勝戦) / `tokusen`(特選・特賞・選抜) / `ippan` / `other`。**整数コードで持ち、対応表は `feature_sets.RACE_KIND_CODES` に固定する**(番号を後から変えるとモデルとずれるため、追加は末尾に足すだけにする)。カテゴリ |
| 7 | rank_number | P | カテゴリ(A1=1…B2=4。順序があるので数値でも可) |
| 8 | age, weight | P | |
| 9 | flying_count, late_count | P | F持ちはスタートを控えるため重要 |
| 10 | average_start_timing | P | |
| 11 | national_win_rate, national_top_2_percent, national_top_3_percent | P | |
| 12 | local_win_rate, local_top_2_percent, local_top_3_percent | P | 当地の出走が少ないと0になるので、`local_win_rate_missing` フラグも付ける |
| 13 | motor_top_2_percent, motor_top_3_percent, boat_top_2_percent, boat_top_3_percent | P | |
| 14 | is_home_branch | P | 支部の都道府県と場の所在地が一致するか(場→都道府県の対応表は `src/common/codes.py`) |
| 15 | r_win_rate_rank | R | 全国勝率のレース内順位(1〜6) |
| 16 | r_win_rate_diff_max | R | 自分の全国勝率 − 他の5艇の最大値 |
| 17 | r_win_rate_diff_mean | R | 自分 − レース平均 |
| 18 | r_st_rank | R | 平均STのレース内順位 |
| 19 | r_motor2_rank | R | モーター2連率のレース内順位 |
| 20 | r_inner_strength | R | 自分より内側の枠の艇の全国勝率の最大値(まくりやすさの代わり)。内側に艇がいない1号艇は 0 |
| 21 | r_a1_count | R | レース内のA1選手の数 |
| 22 | h_racer_n_90d | H | 直近90日の出走数 |
| 23 | h_racer_win_rate_90d, h_racer_top2_90d, h_racer_top3_90d | H | 直近90日の1着率・2連対率・3連対率 |
| 24 | h_racer_avg_finish_90d | H | 平均着順(失格は7として計算する。欠場は除く) |
| 25 | h_racer_course_win_rate | H | 同じコース(前日モデルでは枠番=コースと仮定。過去の側は本番の進入コース `rs_course_number` で数える)での過去の1着率。**期間はデータの開始日〜D−1 の累積**(期間を区切らない)。事前分布の「全体のコース別1着率」も同じく D−1 までの累積。件数が少ないと不安定なので、ベイズ平滑化する: `(勝数 + m × 全体のコース別1着率) / (出走数 + m)`、m=10 |
| 26 | h_racer_real_st_90d | H | 本番STの平均(result の start_timing。フライングの負の値も含める) |
| 27 | h_racer_course_change_rate | H | 枠番と進入コースが異なった割合(前付けの傾向)。期間はデータの開始日〜D−1 の累積 |
| 28 | h_stadium_course_win_rate_365d | H | その場・そのコースの過去365日の1着率(場ごとのイン有利度) |
| 29 | h_motor_series_top2 | H | 同じ節の前日までの、このモーターの2連対率。**節の定義: 同じ場・同じタイトルで、前の開催日との間隔が2日以内**(順延で1日空くことがあるため)。節の判定には出走表の場・タイトル・日付だけを使う(結果は使わない) |
| 30 | h_racer_series_avg_finish | H | 同じ節の前日までの平均着順(予選の調子) |
| 31 | h_days_since_last_race | H | 前回の出走からの日数 |
| 32 | pv_exhibition_time | V | 展示タイム |
| 33 | r_pv_exhibition_rank, r_pv_exhibition_diff_best | V+R | レース内での順位と、最速との差 |
| 34 | pv_start_timing, pv_course_number | V | スタート展示の値(本番とは別物と承知した上で使う) |
| 35 | pv_course_changed | V | pv_course_number が枠番と違うか |
| 36 | pv_tilt_adjustment, pv_weight_adjustment, pv_propeller_new, pv_parts_count | V | |
| 37 | pv_wind_speed, pv_wave_height, pv_air_temperature, pv_water_temperature | V | |
| 38 | pv_wind_class | V | 風向番号を `tail`(3〜7) / `head`(11〜15) / `none`(17) / `cross`(それ以外) に変換したもの(変換は `src/common/codes.wind_class`)。場ごとの補正はしない(API側の注意書きによる)。**整数コードで持ち、対応表は `feature_sets.WIND_CLASS_CODES`(none=0, tail=1, head=2, cross=3)に固定する**。カテゴリ |
| 39 | pv_weather_number | V | カテゴリ |

`pre` は P・H・R の列だけ、`preview` は全部の列を使う。列のリストは `src/features/feature_sets.py` に定数として定義し、モデル担当はそれを import して使う(列名を手で書き写さない)。

## 3. リーク防止

1. **H(過去集計)は、対象日 D より前(D−1以前)の結果だけで計算する**。同じ日の前のレースの結果は使わない(MVP)。実装は「日付ごとに、前日までの累積値をスナップショットする」方式にし、`date` で結合する。行ごとに集計し直すことはしない(遅いうえに、間違いのもとになる)。
2. `rs_*`(本番の気象・進入・ST)、`place_number`、`technique_number`、payouts、odds は特徴量に入れない。ユニットテストで、特徴量の列名に `rs_`・`place`・`odds`・`payout`・`technique` が含まれないことを確認する。
3. 直前の気象は `pv_*` を使う。preview の気象は、同じ場の1つ前のレースの result の気象と同じ値なので、気象の差分特徴は作らない。
4. 過去日の preview は取り直した後の最終値なので、締切前に見えていた値とずれることがある。直前モデルの評価は少し楽観的になる、と `03_model.md` の評価報告に書く。
5. 出走表の勝率・連対率は、公式の出走表に載っている値(その日の結果は含まない)なので、そのまま使ってよい。
6. 選手の欠場・返還(is_absent)は、学習データから行を外す。ただし予想時点では欠場は未知なので、予想は6艇で計算する。欠場が出た場合の確率の再正規化はエクスポート時に行う(`04_export_json.md`)。
7. **「全期間」の集計(#25・#27・節内集計 #29・#30)は、データ開始日から D−1 までの累積(expanding window)とし、D 以降のデータは使わない**。2026-11-03 のPMレビューで実装(`src/features/history.py`)を確認した: 日ごとの累積和を作り、`merge_asof(direction="backward", allow_exact_matches=False)` で「D より厳密に前」の最新の累積を取っているので、時系列のリークは無い。節の判定(`assign_series`)も出走表の場・タイトル・日付だけで決まり、後の日付が前の日の節の番号を変えることは無い。「将来の日を変えても特徴量が変わらない」テストもある。
   - 注意(リークではない): 累積の集計は、データ開始からの日数が長いほど件数が増えるので、学習期間(4〜8月)とテスト期間(10月)で値の安定度が違う。必要ならモデル担当が、件数の特徴(例: `h_racer_course_n`)を追加するよう特徴量担当に依頼する。
8. 過去集計の初期期間(データ開始から90日間)は値が不安定なので、学習データから外すか、`h_*_n` の件数特徴と一緒に使う。どちらにするかはモデル担当がバックテストで決める。

## 4. 当日予想用の特徴量

- `build_features --variant pre --date 2026-11-02` は、当日の出走表と前日までの H を結合し、`data/features/today/20261102_pre.parquet` に出す(目的変数の列はすべてnull)。
- 学習用と当日用で同じ関数を通すこと(学習時と予想時で特徴量の作り方がずれるのを防ぐ)。

## 5. テスト(tests/features/)

- 小さな手作りの生JSON(2日・1場・2レース)を `tests/features/fixtures/` に置き、interim の行数・null・同着の扱いを確認する
- リーク確認: D日の H 特徴が、D日の結果を変えても変わらないこと

## 6. 完了条件

- 2026-01-01〜2026-11-01 の interim と `features_pre.parquet` ができ、レース数・艇数・欠損率を `_quality` に出している
- `feature_sets.py` に `PRE_FEATURES`、`PREVIEW_FEATURES`、`CATEGORICAL_FEATURES`、`RACE_KIND_CODES`、`WIND_CLASS_CODES` がある
- リークのテストが通る

---

## 7. レース運びの特徴量(variant `pre_v2` / `preview_v2`。2026-11-05 追加、F-6)

実装: `src/features/style.py`。列のリストは `feature_sets.STYLE_H_FEATURES`(28列)と `STYLE_R_FEATURES`(4列)。`pre_v2` = `pre` + この32列、`preview_v2` = `preview` + この32列。従来の `pre` / `preview` は変えない。

### 7.1 H 区分(28列 = 14種類 × 期間2つ)

期間は累計(`_all`。データ開始日〜D−1)と直近180日(`_180d`。[D−180, D−1])。

| 列(`{w}` は `all` / `180d`) | 単位 | 内容 |
|---|---|---|
| `h_cs_n_{w}` | 選手×コース | 今回の予定コースでの過去の出走数 |
| `h_cs_nige_{w}`、`h_cs_sashi_{w}`、`h_cs_makuri_{w}`、`h_cs_makurisashi_{w}`、`h_cs_otherwin_{w}` | 選手×コース | 決まり手別の1着率(**出走あたりの率**。その決まり手で1着になった数 ÷ 出走数)。otherwin は抜き・恵まれ・不明 |
| `h_cs_second_{w}`、`h_cs_third_{w}` | 選手×コース | 2着率・3着率 |
| `h_cs_low_{w}` | 選手×コース | 5・6着、失格、F・L の率 |
| `h_cs_st_mean_{w}`、`h_cs_st_std_{w}` | 選手×コース | 本番STの平均・標準偏差 |
| `h_racer_f_rate_{w}`、`h_racer_late_rate_{w}` | **選手単位(全コース)** | フライング(着順コード14)・出遅れ(15)の率 |
| `h_racer_maeduke_rate_{w}` | **選手単位(全コース)** | 枠番より内のコースに入った割合(前付け) |

- 予定コース: 前日版は枠番、直前版は `pv_course_number`(無ければ枠番)。過去の側は本番の進入コース `rs_course_number` で数える。
- **率はすべて全体平均へ縮小する**: `(k + α・p) / (n + α)`、**α = 10**。`p` は D 時点(D−1 までの累計)の全体の率(コース別の列は同じコースの全体平均)。件数 `h_cs_n_*` も特徴量に入れる。
- **D 日の値は D−1 以前の結果だけで作る**(`history.window_sum` を使う。3章と同じ仕組みで、時系列のリークは無い)。

### 7.2 R 区分(4列。累計 `_all` の値を使う)

| 列 | 内容 |
|---|---|
| `r_sty_in_nige` | 予定1コースの艇の逃げ率 |
| `r_sty_nige_minus_sashi2` | 1コースの逃げ率 − 2コースの艇の差し率 |
| `r_sty_nige_minus_makuri34` | 1コースの逃げ率 − 3・4コースの艇の(まくり + まくり差し)率の最大 |
| `r_sty_st_diff_inner` | 自分の平均ST − 内側(予定コースが小さい)艇の平均STの平均。1コースは0 |

### 7.3 当初の仕様(2章)との違い

| 項目 | 2章 | 7章(v2) |
|---|---|---|
| 縮小 | #25 のみ m=10 でコース別1着率へ平滑化 | すべての率を α=10 で全体平均へ縮小 |
| 単位 | 選手・選手×コース・場×コース | F・出遅れ・前付けは**選手単位**、それ以外は**選手×コース** |
| 決まり手 | 使わない | **出走あたりの率**(決まり手別の勝ち数 ÷ 出走数。1着数で割らない) |
| 期間 | 90日・365日・累計 | 累計と180日 |
| 予定コース | 前日版は枠番=コース | 前日版は枠番、直前版は展示の進入コース |

---

## 8. 公式の直前情報の合流と `pv_source`(F-12b。2026-11-06 完了)

`12_beforeinfo.md` の Worker が R2 に保存した公式の直前情報(beforeinfo)を、interim の `pv_*` 列に合流する。

| 列 | テーブル | 値 |
|---|---|---|
| `pv_source` | entries | `official` = 展示タイムかスタート展示(進入・ST)を公式から取った艇、`openapi` = Open API の preview だけの艇、`none` = 直前情報の無い艇 |
| `pv_source` | races | `official` = 1艇でも `official` の艇があるレース、`openapi`、`none` |

規則:
1. **合流するのは、確定していない当日の openapi の日だけ**。確定済みの turnmark の日・旧API(legacy)の日は合流しない(過去日は確定値を使う。02 3章4項。統括承認 2026-11-06)。
2. 公式の値は **項目ごとに優先**する(公式にある項目は公式、無い項目は Open API の値)。
3. 欠場の艇は空(null)にする。
4. 公式のデータの登録番号が出走表と一致しない艇は、公式の値を使わない(取り違えの防止)。
5. 単位・コード値は Open API の preview と同じ(風向・天候のコードは `codes.py`、フライングの ST は負の値)。
6. **確定する予想ファイル**(`data/predictions/YYYYMMDD_preview.parquet`。08 5.1)にも `pv_source` を記録する(モデル担当に依頼済み)。M-18 の `pv_source` 別の成績の集計はこれを使う。
