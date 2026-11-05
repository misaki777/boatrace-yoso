# 旧API(v3)生データのメモ(C-7 → F-7 向け)

## 置き場所
- `data/raw/legacy/{programs,previews,results}/YYYY/YYYYMMDD.json.gz`(2016-08-01〜2025-12-31)
- 中身はレスポンスのバイト列をそのまま gzip したもの。
- 読み込み: `from src.collect.raw_io import load_legacy` → `load_legacy("results", date(2025,12,31))`。無ければ None。
  保存済みの日付: `list_raw_dates("legacy_results")`。
- 取得ログ: `data/raw/_fetch_log.jsonl` の `source` が `legacy_programs` / `legacy_previews` / `legacy_results` の行。
  その日・その種類の最後の行が 200 なら取得済み、404 ならデータなし。

## 形式(2016-08-01 と 2025-12-31 で確認。キー構成は同じ)
トップレベルは `{種類名: [レース, ...]}` の配列(v1 のような場→レースの dict ではない)。レースの識別は `date` + `stadium_number` + `number`(R番号)。

| 種類 | レースのキー | 艇(`boats` 配列)のキー |
|---|---|---|
| programs | date, stadium_number, number, closed_at, day_label, grade_label, grade_number, title, subtitle, distance, boats | racer_boat_number(枠番), racer_name, racer_number(登録番号), racer_class_number, racer_branch_number, racer_birthplace_number, racer_age, racer_weight, racer_flying_count, racer_late_count, racer_average_start_timing, racer_national_top_{1,2,3}_percent, racer_local_top_{1,2,3}_percent, racer_assigned_motor_number, racer_assigned_motor_top_{2,3}_percent, racer_assigned_boat_number, racer_assigned_boat_top_{2,3}_percent |
| previews | date, stadium_number, number, wind_speed, wind_direction_number, wave_height, weather_number, air_temperature, water_temperature, boats | racer_boat_number, racer_course_number, racer_start_timing, racer_weight, racer_weight_adjustment, racer_exhibition_time, racer_tilt_adjustment |
| results | date, stadium_number, number, wind_speed, wind_direction_number, wave_height, weather_number, air_temperature, water_temperature, technique_number, payouts, boats | racer_boat_number, racer_course_number, racer_start_timing, racer_place_number, racer_number, racer_name |

- `payouts`: `{"trifecta": [{"combination": "3-1-4", "amount": 1420}], "trio": [...], "exacta", "quinella", "quinella_place", "win", "place"}`(v1 と同じ券種名・組合せ表記。amount は100円あたりの払戻)。
- v1 との主な違い: `number`→`race_number`、`racer_boat_number`→`entry_number`、`racer_place_number`→`place_number`、`racer_national_top_1_percent`→`national_win_rate`(名前は top_1_percent だが値は勝率。例 2.75。v1 の national_win_rate と同じ意味か要確認)、`racer_class_number`→`rank_number`、`*_source` の文字列項目は無い、`day_label`/`grade_label` は文字列。preview の部品交換・プロペラ、オッズは無い。
- 1日あたり 3ファイル合計 非圧縮 約1.4MB(gzip 約110KB)。全期間で約0.4GBの見込み。
