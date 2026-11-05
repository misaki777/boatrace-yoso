# 04 公開JSON仕様(src/export/ → docs/data/)

担当: エクスポート担当(JSONを作る側)。PWAフロント担当(JSONを読む側)と、デザイナー(どんな情報があるかを確認する人)も読む。

改訂履歴:
- 2026-11-03(3回目): 公式サイトの締切前オッズに対応。**`07_official_odds_and_settings.md` 3章と6章が優先する**。`odds_snapshot`、picks・recommended の `ev_rank` を追加。締切前オッズ・確定オッズの **数値(`odds`・`ev`)は公開JSONから外し、ローカルJSON(`docs/data-local/`、git管理外)にだけ入れる**(`final_odds` の数値の公開もやめる。E-7)
- 2026-11-03(2回目): デザイナーAの確認を受けて `strategy.short_label`、`performance.live.strategy_label`、`backtest.strategies[].short_label` を追加(任意項目の追加なので `schema_version` は 1 のまま)。**エクスポート担当への指示: E-2/E-3 の出力とサンプル(`docs/data/sample/`)にこの3項目を追加し、`schema.py` の検証にも加える**
- 2026-11-03: E-1/E-2 の実装からのフィードバックを承認して反映(`recommended_outcome.refunded`、サンプルの戦略、中止レースの扱い、`order` は常に3要素、`final_odds` の null と `[]` の区別、集計項目の定義、ファイルサイズの実測値)。`schema_version` は 1 のまま(既存の項目を壊す変更ではないため)

このファイルが **PythonとPWAの間の契約** になる。項目を変えるときは、エクスポート担当がこのファイルの改訂案を出し、PMが承認してから実装する。後方互換の無い変更をするときは `schema_version` を上げる。

---

## 1. 公開にあたっての配慮(公開リポジトリのため)

- 生データ(openapi・turnmarkのJSON)を丸ごと、または大部分をそのまま再配布しない。出すのは **画面表示に必要な最小限の項目** と、本ツールが作った予想の値だけにする。
- 出さないもの: 全券種の確定オッズの表、全券種の払戻の表、`_source` 文字列、選手の出身地・年齢・体重・F/L以外の細かい成績、直前情報のチルト・部品交換などの詳細、モデルの特徴量。
- 出すもの: 場名・R番号・締切時刻・タイトル、選手名・登録番号・級別・支部・主な成績(数項目)、展示タイム・展示の進入(直前予想があるとき)、予想確率、推奨買い目、結果(着順、決まり手、推奨した券種の払戻だけ)。
- 出典の表示: `index.json` の `sources` にデータ元(Boatrace Open API / Turnmark API。どちらもMIT、非公式)を書き、PWAの「このアプリについて」で表示する。
- 個人の資金情報(実際に買った額など)は扱わない。回収率はすべて「1点100円で買ったと仮定したシミュレーション」として出す。
- JSON内の文字列はUTF-8、日時はJSTのISO 8601(`+09:00` 付き)。数値の確率は小数4桁、オッズは小数1桁に丸める(ファイルを小さくするため)。

## 2. ファイル構成

```
docs/data/
  index.json               日付一覧と、各日の概要(PWAが最初に読む)
  days/20261102.json       1日分の全レース(予想 + 結果)
  performance.json         成績の集計(実運用とバックテスト)
  sample/                  フロント開発用の固定サンプル(エクスポート担当が最初に作る。中身は架空のデータでよい)
    index.json
    days/20261102.json
    performance.json
```

- 1日1ファイル。当初の想定は約400KBだったが、実測(E-2)では約4.5KB/レースで、150レースなら約680KB(GitHub Pagesはgzipで配信するので、転送は約80KB)。
- 1日分のファイルを、場ごとやレースごとにさらに分けることはしない(PWAの読み込みを単純にするため)。300KB(gzip後)を超えるようになったら見直す。
- `days/` の保管期間は MVP では無制限。Phase 3 で、120日より古いファイルを消して `performance.json` に集計だけ残すかどうかを決める(未決事項)。
- 書き出しは一時ファイル経由のアトミックな置き換えで行い、`index.json` は最後に更新する(PWAが書きかけのファイルを読まないようにする)。

CLI:
```
python -m src.export.export_day --date 2026-11-02     # days/20261102.json を作る、または更新する(予想・結果のうち、あるものを入れる)
python -m src.export.export_index                     # index.json と performance.json を作り直す
python -m src.export.make_sample                      # docs/data/sample/ を作る
```

## 3. index.json

```json
{
  "schema_version": 1,
  "generated_at": "2026-11-02T21:05:00+09:00",
  "latest_date": "2026-11-02",
  "sources": [
    {"name": "Boatrace Open API", "url": "https://github.com/boatraceopenapi/api", "license": "MIT"},
    {"name": "Turnmark API", "url": "https://github.com/turnmark/api", "license": "MIT"}
  ],
  "dates": [
    {
      "date": "2026-11-02",
      "file": "days/20261102.json",
      "status": "in_progress",
      "stadium_count": 13,
      "race_count": 156,
      "summary": {"races_with_result": 133, "bets": 665, "hits": 21, "stake": 66500, "return": 48200, "roi": 0.725}
    },
    {
      "date": "2026-11-01",
      "file": "days/20261101.json",
      "status": "final",
      "stadium_count": 14,
      "race_count": 168,
      "summary": {"races_with_result": 168, "bets": 840, "hits": 30, "stake": 84000, "return": 91300, "roi": 1.087}
    }
  ]
}
```

- `dates` は新しい順に並べる。
- `status`: `predicted`(予想のみ) / `in_progress`(結果の反映途中) / `final`(全レース確定。turnmarkの確定オッズの反映済み)
- `summary` は、推奨買い目(`recommended`)を1点100円で買ったと仮定した値。結果が無ければ null。

## 4. days/YYYYMMDD.json

```json
{
  "schema_version": 1,
  "date": "2026-11-02",
  "generated_at": "2026-11-02T21:05:00+09:00",
  "status": "in_progress",
  "model": {"model_id": "pre_20261101_a1b2", "variant": "pre", "train_until": "2026-10-31"},
  "strategy": {"name": "top_k", "bet_type": "trifecta", "k": 5, "short_label": "3連単 上位5点", "description": "3連単 確率上位5点"},
  "stadiums": [
    {
      "stadium_number": 2,
      "stadium_name": "戸田",
      "title": "第58回報知新聞社杯",
      "grade": "一般",
      "day_number": 3,
      "races": [
        {
          "race_id": "20261102-02-01",
          "race_number": 1,
          "closed_at": "2026-11-02T10:45:00+09:00",
          "subtitle": "予選",
          "state": "finished",
          "prediction": {
            "variant": "pre",
            "predicted_at": "2026-11-02T07:30:00+09:00",
            "confidence": 0.412,
            "boats": [
              {
                "entry": 1,
                "racer_name": "高山 弘斗",
                "racer_number": 5242,
                "rank": "B1",
                "branch": "埼玉",
                "national_win_rate": 3.59,
                "local_win_rate": 3.5,
                "motor_top2": 44.4,
                "avg_st": 0.18,
                "flying_count": 0,
                "exhibition_time": null,
                "exhibition_course": null,
                "p_win": 0.2810,
                "p_top2": 0.4950,
                "p_top3": 0.6620,
                "p_rank": 2
              }
            ],
            "picks": {
              "win":      [{"combination": "2", "prob": 0.4120, "required_odds": 2.5}],
              "exacta":   [{"combination": "2-1", "prob": 0.1310, "required_odds": 7.7}],
              "trifecta": [{"combination": "2-1-3", "prob": 0.0520, "required_odds": 19.3}]
            },
            "recommended": [
              {"bet_type": "trifecta", "combination": "2-1-3", "prob": 0.0520, "required_odds": 19.3},
              {"bet_type": "trifecta", "combination": "2-1-6", "prob": 0.0410, "required_odds": 24.4}
            ]
          },
          "result": {
            "order": [2, 1, 6],
            "technique": "差し",
            "absent_entries": [],
            "payouts": {
              "win":      [{"combination": "2", "amount": 400}],
              "exacta":   [{"combination": "2-1", "amount": 490}],
              "trifecta": [{"combination": "2-1-6", "amount": 2750}]
            },
            "recommended_outcome": {"bets": 5, "hits": 1, "stake": 500, "return": 2750},
            "final_odds": [
              {"bet_type": "trifecta", "combination": "2-1-3", "odds": 18.2, "ev": 0.95},
              {"bet_type": "trifecta", "combination": "2-1-6", "odds": 27.5, "ev": 1.13}
            ]
          }
        }
      ]
    }
  ],
  "day_summary": {"races_with_result": 133, "bets": 665, "hits": 21, "stake": 66500, "return": 48200, "roi": 0.725}
}
```

### 項目の定義

| パス | 型 | 説明 |
|---|---|---|
| `stadiums[]` | | 場番号の昇順 |
| `.grade` | str | `SG` / `PG1` / `G1` / `G2` / `G3` / `一般`(コード値を文字に変換したもの) |
| `races[]` | | R番号の昇順 |
| `.closed_at` | str | 締切日時。PWAは端末の現在時刻と比べて「締切前/締切後」を判断する |
| `.state` | str | 出力した時点の状態: `scheduled`(予想のみ) / `preview`(直前情報で予想済み) / `finished`(結果あり) / `canceled`(中止) / `no_prediction`(予想できなかったレース。例: 出走表が欠けている) |
| (中止レース) | | `state: "canceled"` のとき、`prediction` は出した予想をそのまま残し、`result` は null にする。`recommended_outcome` も無いので、`day_summary`・`index.json` の `summary`・`performance.json` の集計から外す(買わなかった扱い。返還として数えない) |
| `prediction` | obj/null | 予想が無いときは null |
| `.confidence` | float | 1着確率の最大値(レースの「堅さ」を表す) |
| `.boats[]` | | 枠番の順に6艇 |
| `.boats[].rank` / `.branch` | str | 級別(A1〜B2)、支部の都道府県名(「県」を除いたもの) |
| `.boats[].motor_top2` | float | モーター2連率(%) |
| `.boats[].exhibition_time` / `.exhibition_course` | float/int/null | 直前予想(variant=preview)のときだけ値が入る |
| `.boats[].p_win/p_top2/p_top3` | float | 1着・2着以内・3着以内の確率 |
| `.boats[].p_rank` | int | p_win のレース内順位(1が最上位)。◎○▲などの印をどう表現するかはデザイナーに任せる |
| `.picks` | obj | 券種ごとの確率上位の組合せ。単勝は6点すべて、2連単は上位10点、3連単は上位20点。確率の降順 |
| `.required_odds` | float | 必要オッズ = 1/prob(小数1位で切り上げ) |
| `.recommended[]` | | 戦略(`strategy`)で選んだ買い目。0件のこともある(見送り) |
| `strategy` | obj | 推奨買い目を選んだ戦略。`name` は `03_model.md` 6章の戦略名(`top_k` / `confidence_skip` など)で、`params` 相当の値(`bet_type`、`k`、`alpha` など)を同じオブジェクトに持つ。**実運用の既定は `top_k`(3連単、k=5)**(未決事項U2で見直す)。サンプル(`docs/data/sample/`)は見送りの表示を確かめるため `confidence_skip`(k=5、alpha=0.35)を使う。PWAは戦略名を決め打ちせず、表示には `short_label` を使う(詳しい説明が必要な画面では `description`) |
| `strategy.short_label` | str | (2026-11-03 追加)戦略の短い表示名。**14文字以内**(スペースも1文字と数える)。エクスポート担当が `bet_type` と戦略のパラメータから生成する。例: `top_k` → `3連単 上位5点`、`confidence_skip` → `3連単 上位5点・荒れ見送り`、`prob_threshold` → `3連単 確率5%以上`。PWAは、この項目が無い古いファイルでは `description` で代わりに表示する |
| `result` | obj/null | 結果が無いときは null |
| `.order` | (int\|null)[] | 1〜3着の枠番。**常に3要素**。同着や失格で決まらない位置は null にする(例: `[2, 1, null]`) |
| `.absent_entries` | int[] | 欠場・返還になった枠番 |
| `.payouts` | obj | **予想で扱う券種(単勝・2連単・3連単)の的中分だけ**。特払・不成立は `{"combination": null, "amount": 70, "label": "特払"}` |
| `.recommended_outcome` | obj | 推奨買い目を1点100円で買ったと仮定した場合の、投資と払戻。`{bets, hits, stake, return}` に加え、任意で `refunded`(欠場・返還で返還された点数。int。返還が無ければ省略してよい)。返還分の100円は `return` に含める |
| `.final_odds` | arr/null | (**2026-11-03 変更: 公開JSONでは各要素を `{bet_type, combination, ev_rank}` だけにし、`odds`・`ev` の数値はローカルJSONにだけ入れる。07 6章**)翌日、turnmarkの確定オッズを取り込んだ後に入る。**推奨買い目の分だけ**(オッズ表の全体は出さない)。**null = 確定オッズをまだ読み込んでいない**、**`[]` = 読み込み済みだが該当する買い目が無い**(見送りのレースなど)。PWAはこの2つを区別して表示する |

### 欠場があった場合

予想後に欠場が出た場合(result.absent_entries が空でないとき)、`prediction` は書き換えず、`result.recommended_outcome` で返還分を払戻として計上する。直前予想(Phase 2)では、欠場艇を外して計算し直した確率を `prediction` に入れる。

## 5. performance.json

```json
{
  "schema_version": 1,
  "generated_at": "2026-11-02T21:05:00+09:00",
  "live": {
    "strategy": "top_k(trifecta,k=5)",
    "strategy_label": "3連単 上位5点",
    "from": "2026-11-01",
    "to": "2026-11-02",
    "total": {"races": 301, "bets": 1505, "hits": 51, "stake": 150500, "return": 139500, "roi": 0.927, "hit_rate": 0.169},
    "by_month": [{"month": "2026-11", "races": 301, "stake": 150500, "return": 139500, "roi": 0.927}],
    "by_day": [{"date": "2026-11-02", "stake": 66500, "return": 48200, "roi": 0.725, "cum_roi": 0.927}]
  },
  "backtest": {
    "model_id": "pre_20261101_a1b2",
    "period": {"from": "2026-10-01", "to": "2026-10-31"},
    "metrics": {"win_logloss": 1.312, "top1_acc": 0.512, "trifecta_top10_hit": 0.331},
    "strategies": [
      {"name": "top_k", "params": {"bet_type": "trifecta", "k": 5}, "roi": 0.81, "roi_ci95": [0.70, 0.93], "hit_rate": 0.17, "leak_free": true},
      {"name": "ev_final", "params": {"bet_type": "trifecta", "tau": 1.2}, "roi": 1.05, "roi_ci95": [0.82, 1.31], "hit_rate": 0.04, "leak_free": false}
    ]
  }
}
```

- `live`: 本運用で出した予想の、実際の結果に基づく集計。`strategy_label`(2026-11-03 追加)は `days` の `strategy.short_label` と同じ規則で作る。`backtest.strategies[]` にも同じ規則の `short_label` を付ける。
- 集計項目の定義(`live`・`backtest`・`index.json` の `summary`・`day_summary` で共通):
  - `races`: **推奨買い目を1点以上買ったレースの数**(見送り・中止のレースは含めない)
  - `bets`: 買った点数、`hits`: 的中した点数
  - `hit_rate` = **1点以上的中したレースの数 ÷ `races`**(点数単位ではなくレース単位)
  - `roi` = `return` ÷ `stake`(`stake` が0なら null)
- `backtest`: 採用中のモデルの test 期間の成績。`leak_free: false` の戦略は、PWAで「参考値(後知恵)」とわかるように表示する。

## 6. 検証とテスト

- `src/export/schema.py` に、JSONの構造をチェックする関数(必須キー・型・値の範囲)を、標準ライブラリだけで書く(jsonschemaライブラリは使わない)。書き出す前に必ず通す。
- テスト(tests/export/): サンプルの予想・interimから JSON を作り、(a) スキーマに合う、(b) `picks.win` の prob の合計が1、(c) 公開してはいけない項目(`odds` の表全体、`_source`、`age`、`weight` など)が含まれない、ことを確認する。

## 7. 完了条件

- `docs/data/sample/` がこの仕様どおりにある(フロント開発の最初の依存なので最優先)
- 2026-11-01 と 2026-11-02 の実データで `days/*.json`、`index.json`、`performance.json` を出力できる
- 同じ日を2回出力しても、`generated_at` 以外は差分が出ない(冪等である)

---

## 8. Phase 3a の追加(2026-11-04。仕様の根拠は `08_personas_and_features.md` 5章)

いずれも項目の追加なので `schema_version` は 1 のまま。PWA は項目が無いファイル(過去日)でも動くこと。

### 8.1 days/YYYYMMDD.json

| パス | 型 | 内容 |
|---|---|---|
| `prediction.locked_at` | str | 予想を確定した時刻(08 5.1)。**一度出したら変えない** |
| `prediction.version` | str | `pre`(前日版)/ `preview`(直前版)。表示用の版(F02) |
| `prediction.model_id` | str | その予想を出したモデル |
| `prediction.boats[].reasons` | arr | 確率の根拠トップ3(08 5.2)。`[{"label": "全国勝率", "value": 6.85, "direction": "up", "strength": 3}]`。`strength` は寄与の大きさを1〜3の段階にしたもの(数値の寄与は出さない)。`value` は出走表の値か自前の集計値だけ |
| `prediction.skip_reason` | str/null | 推奨が0件のときの理由: `low_confidence` / `low_prob` / `low_ev` / null(F12) |
| `preview_info` | obj/null | 直前情報の表示用(F13。予想とは別): `{"boats": [{"entry": 1, "exhibition_time": 6.62, "exhibition_course": 1, "exhibition_st": 0.17}], "weather": "曇り", "wind_speed": 0, "wind_class": "none", "wave_height": 0}` |

サイズ: `reasons` は1艇3件 × 約60バイト ≒ 1レース約1.1KB、150レースで約+160KB(gzip後 約+15KB)。`preview_info` は1レース約0.3KB。合わせて1日 約+210KB(gzip後 約+20KB)。gzip後300KBの見直し基準には収まる。

### 8.2 performance.json

```json
"live": {
  "by_version": {"pre": {"races": 117, "...": "..."}, "preview": null},
  "models": [{"model_id": "pre_20261101_a1b2", "from": "2026-11-02", "to": "2026-11-04"}],
  "calibration": [{"bin": "50-60", "n": 210, "pred_mean": 0.548, "actual": 0.481, "ci95": [0.414, 0.548]}],
  "strategies": [
    {"name": "top_k", "short_label": "3連単 上位5点", "races": 117, "bets": 585, "roi": 0.505, "roi_ci95": [0.31, 0.74],
     "hit_rate": 0.12, "max_losing_streak": 14, "max_drawdown_yen": 31200, "is_baseline": false},
    {"name": "baseline_win1", "short_label": "1号艇 単勝", "races": 117, "...": "...", "is_baseline": true}
  ],
  "sample_note": "few"
},
"backtest": {
  "calibration": [...],
  "strategies": [... 同じ形。leak_free を持つ ...],
  "by_month": [{"month": "2026-10", "races": 4580, "roi": 0.78, "roi_ci95": [0.71, 0.86]}]
}
```

- 全ての集計に件数(`n` / `races`)を付ける。`sample_note`: レース数が200未満なら `"few"`、それ以外は null(PWA は `few` のとき数字を薄く表示する。08 4章)。
- 較正の95%区間は Wilson、回収率の95%区間はレース単位のブートストラップ(1,000回)。
- `live` は**確定版の予想だけ**から集計する(08 5.1)。
- 基準の戦略(`is_baseline: true`)として「1号艇 単勝」「1号艇から3連単 上位5点」を必ず含める。
- サイズ: 数KB〜十数KBの増加。

### 8.3 公開してはいけない項目(追加)

購入記録・印・メモ・予算は JSON に一切含めない(端末内だけ)。`reasons` に寄与の生の数値を入れない。
