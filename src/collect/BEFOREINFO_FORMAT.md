# 公式 直前情報ページ(beforeinfo)の構造メモ(C-8 → R-4・F-12b・PM 向け)

確認日: 2026-11-06 15:24 JST。PC から3ページ取得(1.5秒以上の間隔): 03-08(締切後)、01-02(締切21分前)、01-03(締切46分前)。
実物のHTMLは `data/raw/beforeinfo_samples/`(git管理外)。リポジトリには合成HTMLだけを置く(`tests/fixtures/beforeinfo/`)。

URL: `https://www.boatrace.jp/owpc/pc/race/beforeinfo?rno={R}&jcd={場2桁}&hd={YYYYMMDD}`(約45KB、UTF-8)

## 切り出し方(Python `src/collect/beforeinfo.py` と JS で同じにする)

正規表現と文字列の検索だけで読める(DOM パーサは不要)。数値は `-?(\d+(\.\d+)?|\.\d+)` の最初の一致。
`&nbsp;` と全角空白は空白に置き換え、タグを除いてから読む。

### 1. 選手の表

- `<table class="is-w748"` から次の `</table>` まで。
- その中の `<tbody ...>...</tbody>` が **ちょうど6個**(枠番1〜6の順)。6個でなければ解析エラー。
- 各 tbody の `<td ...>...</td>` を順番に並べた位置(td の入れ子は無い):

| 位置 | 中身 | 例 | 出力 |
|---|---|---|---|
| 0 | 枠番 | `1` | キー |
| 2 | 名前。`href` の `toban=` が登録番号 | `toban=3929` | racer_number |
| 3 | 体重 | `51.5kg` | weight |
| 4 | 展示タイム(未掲載は `&nbsp;`) | `6.96` | exhibition_time |
| 5 | チルト(締切の40分以上前は空のことがある) | `-0.5` | tilt_adjustment |
| 6 | プロペラ。交換時は `新` | `&nbsp;` | propeller_new |
| 7 | 部品交換。`<ul class="labelGroup1">` の `<li>` | 空 | parts |
| 12 | 調整重量 | `0.5` | weight_adjustment |

- 位置 8〜11・13〜16 は「前走成績」(R・進入・ST・着順)。**今回の展示ではないので使わない**(進入・ST の取り違えに注意)。
- 部品交換の `<li>` の中身はラベル名(凡例: ピストン・リング・電気・キャブ・シリンダ・シャフト・ギヤ・キャリボ)。
  Open API と同じく `{"number_source": 名前, "number": 1〜8(この順), "quantity": 個数 or null}` にする。
  個数の表記(`リング×2` など)は今回の実物に交換が無く**未確認**。`×/x/✕` + 数字(全角可)を個数として読む。
- ページ下部にも凡例の `labelGroup1` があるが、表の外なので位置7の td だけを見れば混ざらない。

### 2. スタート展示

- `<table class="is-w238"` から `</table>`、その中の `<tbody` 〜 `</tbody>` の `<tr>` が **上から進入コース1〜6**。
- 各行の `class="table1_boatImage1Number ...">艇番</span>` と `class="table1_boatImage1Time">ST</span>`。
- 未掲載のときは6行とも `<td colspan="3"></td>` で中身が空(締切21分前の実物で確認)。
- ST: `.03` → 0.03。フライングは `F.03` → **-0.03 と start_flying=true**(Open API の `start_timing_source` の表記 `F.02` と同じ前提。実物の F はまだ見ていない)。
- 前付け: 行の順がそのまま進入コース。艇番が行番号と違えば前付け。
- 欠場艇はスタート展示の行に出てこない前提(残りの艇が上から詰まる)。**実物の欠場は未確認**。

### 3. 気象

- `<div class="weather1"` から `class="weather1_stand"` まで。
- 天候コード: `is-weather{N}`(1=晴 …。`codes.WEATHER_NAMES` と同じ番号の前提)。
- 風向コード: `is-wind{N}`(1〜16=方位、17=無風。`codes.WIND_DIRECTION_NAMES` と同じ番号の前提)。
  `is-direction{N}`(場の向き)と `is-windDirection`(div の class)は別物なので、**`is-wind` の直後が数字のもの**だけを読む。
- `weather1_bodyUnitLabelTitle">名前</span>` の直後の `weather1_bodyUnitLabelData">値</span>` の組: 気温 `22.0℃`、風速 `4m`(整数)、水温 `22.0℃`、波高 `5cm`(整数)。天候の Title(`晴`)には Data が無いので、`[^<]*` で読む(`.*?` だと次の組まで食う)。
- `weather1_title` は `水面気象情報　14:55現在` や `7R時点`(締切後のページ)。`as_of` として残す。

## 状態の判定(status)

- 欠場(absent): 体重とチルトがどちらも空で、スタート展示にも出てこない艇(他の艇には体重かチルトがある)。
- 出走艇(欠場以外)について:
  - `not_posted`: 展示タイムもスタート展示も1艇も無い
  - `partial`: どれかの艇で展示タイムかスタート展示が欠けている
  - `ok`: 全艇に展示タイムとスタート展示がある
- 実物の観察: 締切46分前 = 体重のみ(チルトも空)、締切21分前 = 体重・チルトはあり展示は空、締切後 = 全部あり。

## 出力(1レース)

```json
{"race_id": "20261106-02-01", "fetched_at": "...", "minutes_before_close": 12.8, "attempt": 1, "status": "ok",
 "weather": {"weather_number": 2, "wind_direction_number": 17, "wind_speed": 0, "wave_height": 1,
             "air_temperature": 17.0, "water_temperature": 18.0, "as_of": "10:31現在"},
 "boats": {"1": {"racer_number": 9001, "exhibition_time": 6.71, "tilt_adjustment": -0.5, "weight": 52.1,
                 "weight_adjustment": 0.0, "propeller_new": false, "parts": [], "start_course": 1,
                 "start_timing": 0.11, "start_flying": false, "absent": false}}}
```

spec/12 3章の例に `racer_number`・`start_flying`・`absent`・`weather.as_of` を足している(PM に追記を依頼)。

## 合成 fixture

`tests/fixtures/beforeinfo/{not_posted,partial,ok,flying,absent,maeduke}.synthetic.html` と `*.expected.json`。
作り直しは `python -m src.collect.make_beforeinfo_fixtures`(期待値は解析関数を通さず入力から作る)。
選手名・登録番号(9001〜9006)・数値はすべて架空。

## 手動取得(PC)

`python -m src.collect.beforeinfo --race 20261106-02-01 [--save-html]` → `data/raw/beforeinfo/YYYYMMDD/SS-RR_HHMMSS.json.gz`。
取得ログは `_fetch_log.jsonl`(source=beforeinfo)。前回の beforeinfo アクセスから1.5秒未満なら待つ。
`--html FILE` は手元のファイルを解析するだけ(アクセスなし)。
