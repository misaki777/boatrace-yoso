# 03 モデル仕様(src/model/)

担当: モデル担当。入力は `data/features/` と `data/interim/`(payouts・odds)、出力は `models/`、`data/predictions/`、`reports/`(いずれもgit管理外)。

## 1. 方針

- CPUで動く無料の手法に限る。主力は LightGBM。基準線(ベースライン)には、ルールで作る確率とロジスティック回帰を使う。
- 各艇の「強さ」を1つ推定し、それを確率モデルで組合せの確率に広げる。組合せごとに直接学習することはしない(3連単は120通りあり、データが足りないため)。

## 2. 目的変数と確率の計算

### 2.1 1着確率(MVP)

- 学習: 1行=1艇、目的変数は `is_win`。LightGBM の `objective=binary` で、出力はスコア `s_i`(logit)。
- レース内での正規化: `p_i = exp(s_i / T) / Σ_j exp(s_j / T)`(6艇で合計が1になるsoftmax)。温度 `T` は検証期間の多クラスloglossが最小になるように1次元探索で決める(初期値1.0)。
- 代わりの案(Phase 2で比較): `objective=multiclass` は艇単位の行にはそのままでは合わない。`lambdarank`(レースを group にし、着順から関連度を作る)のスコアを同じsoftmaxで確率にする案を比べる。

### 2.2 組合せ確率(Harville / Plackett-Luce)

1着確率 `p` から、Plackett-Luce(Harville式)で順序付きの確率を出す。

```
P(1着=i)                 = p_i
P(1着=i, 2着=j)          = p_i * p_j / (1 - p_i)
P(1着=i, 2着=j, 3着=k)   = p_i * p_j / (1 - p_i) * p_k / (1 - p_i - p_j)
```

ここから各券種の確率を作る。

| 券種 | 計算 |
|---|---|
| win 単勝 | `p_i` |
| exacta 2連単 | 上の2着までの式 |
| quinella 2連複 | `P(i,j) + P(j,i)` |
| trifecta 3連単 | 上の3着までの式(120通り。合計が1になることを確認する) |
| trio 3連複 | 6通りの並びの和 |
| place 複勝(2着以内) | `Σ_j P(i,j) + Σ_j P(j,i)` |
| quinella_place 拡連複(3着以内に2艇) | 3連単の確率のうち、i と j がどちらも3着以内に入るものの和 |

**Harvilleの補正(Phase 2)**: Harville式は、2着・3着で人気薄の確率を高く見積もりがちなことが知られている。Henery/Stern 型の補正として、2着の計算では `p^γ2`、3着の計算では `p^γ3` を正規化したものを使う。`γ2, γ3 ∈ [0.5, 1.2]` は、検証期間の3連単logloss(実際の着順の組合せの -log P)が最小になるようにグリッド探索で決める。MVPでは γ=1(補正なし)。

**欠場艇**: 予想は6艇で行う。当日に欠場が判明した艇は `p_i=0` にして残りを正規化し直し、組合せ確率を計算し直す(export時に処理する。関数は `src/model/combinations.py` に用意し、エクスポート担当はそれを呼ぶ)。

### 2.3 ベースライン(比較用。必須)

- B0: 全レースでのコース別1着率(枠番=コースと仮定)を `p_i` とし、Harvilleで計算したもの
- B1: B0 × 全国勝率でロジスティック回帰したもの
- LightGBMは、少なくとも B1 より logloss で良くなければ採用しない。

## 3. 学習・検証の分け方(時系列)

データは2026-01-01以降の約10か月(約4.6万レース、約27万艇行)。シャッフルはしない。

| 区間 | 期間(初回) | 用途 |
|---|---|---|
| warm-up | 2026-01-01〜2026-03-31 | H特徴量を作るためだけに使う(学習しない。`02_features.md` 3章7項) |
| train | 2026-04-01〜2026-08-31 | 学習 |
| valid | 2026-09-01〜2026-09-30 | early stopping、温度T、γ、買い方の閾値の決定 |
| test | 2026-10-01〜2026-10-31 | 最終評価。**ここを見て設定を変えない** |

- 運用中の再学習: 月に1回、「直近 N か月で学習し、翌月を予想する」ウォークフォワードで行う。期間の設定は `src/model/config.py` に置く。
- warm-upの長さ(90日)は、H特徴量を外したモデルとの比較で見直してもよい。
- 乱数のseedは固定する。`meta.json` に、学習した期間・特徴量のリスト・ハイパーパラメータ・データのハッシュを残す。

### meta.json の必須キー(export が読む。2026-11-03 追加)

```json
{
  "model_id": "pre_20261101_a1b2",
  "variant": "pre",
  "train_until": "2026-08-31",
  "periods": {"train_from": "2026-04-01", "train_to": "2026-08-31", "valid_from": "2026-09-01", "valid_to": "2026-09-30", "test_from": "2026-10-01", "test_to": "2026-10-31"},
  "temperature": 1.0,
  "harville_gamma": null,
  "features": ["..."],
  "params": {},
  "seed": 42,
  "data_hash": "..."
}
```

- `train_until` は `periods.train_to` と同じ値を必ず入れる(export は `train_until` を読み、無ければ `periods.train_to` を使う)。
- `harville_gamma`: γ補正(2.2節)を使わないときは null。使うときは `{"gamma2": 0.85, "gamma3": 0.75}` の形で出す。export はこの値を `combinations.py` に渡して組合せ確率を計算するので、**predict と export で同じγが使われることを保証する唯一の場所** になる。

### LightGBMの初期パラメータ(目安)

`num_leaves=31, learning_rate=0.03, min_data_in_leaf=100, feature_fraction=0.8, bagging_fraction=0.8, bagging_freq=1, lambda_l2=1.0, num_boost_round=5000, early_stopping=200`。カテゴリ列は `categorical_feature` に指定する。

## 4. 評価指標

`python -m src.model.evaluate --model-id ...` で、valid と test それぞれについて出す。

| 指標 | 定義 | 目的 |
|---|---|---|
| win_logloss | レースごとに `-log p_(実際の1着艇)` の平均 | **主指標** |
| win_brier | 6艇分の二乗誤差 | 補助 |
| top1_acc | 確率が最大の艇が1着になった割合 | わかりやすさ |
| trifecta_logloss | `-log P(実際の3連単)` の平均 | 組合せ確率の質 |
| trifecta_top10_hit | 確率の上位10点に実際の3連単が入った割合 | |
| calibration | 確率を10分位に分けたときの、予測の平均と実際の割合 | 期待値計算の前提になる(図は Phase 2) |
| 場別・コース別の logloss | | 弱いところを探す |

中止レース・同着1着・返還艇のあるレースは評価から外す(外した件数は報告する)。

## 5. 期待値

```
EV(組合せ c) = P(c) × odds(c)        (odds は100円あたりの払戻倍率)
必要オッズ(c) = 1 / P(c)              (EV=1 になるオッズ)
```

- 当日はオッズが無いため、PWAには `required_odds`(必要オッズ)を出す。表示用には `ceil(required_odds × 10) / 10` で丸める。
- 確定オッズがあるとき(翌日以降)は、`ev_final = P(c) × 確定オッズ` を記録する。
- 控除率(約25%)があるので、市場が効率的なら EV は平均で0.75程度になる。EV>1 が出るのは、モデルと市場の評価がずれたときだけ。**確率の較正(calibration)がずれていると、EVは系統的に過大になる**ので、較正の確認を EV 戦略の前提とする。

## 6. 回収率シミュレーション(バックテスト)

`python -m src.model.backtest --model-id ... --period test --strategy <name>`

- 賭け金は1点100円で一定とする(資金管理は扱わない)。
- 払戻は interim の payouts を使う(同着で払戻が複数ある場合はすべて計上し、返還・特払・不成立は返還額で計上する)。
- 出力: `reports/backtest/{model_id}/{strategy}_{period}/summary.json`(点数、投資額、払戻額、回収率、的中率、最大ドローダウン、月別の回収率)と `bets.parquet`(1行=1点)。
- 集計項目の定義は `04_export_json.md` 5章と同じにする。特に **的中率 `hit_rate` は「1点以上的中したレース数 ÷ 買ったレース数」**(点数単位ではない)、`races` は買ったレースだけを数える。

| 戦略 | 内容 | オッズの使い方 | 注意 |
|---|---|---|---|
| S1 `top_k` | 3連単の確率の上位k点(k=1,3,5,10)を全レースで買う | 使わない | **リークなし。MVPの基準** |
| S2 `prob_threshold` | P(c) ≥ θ の組合せを買う | 使わない | リークなし |
| S3 `ev_final` | P(c) × 確定オッズ ≥ τ の組合せを買う(τ=1.0, 1.2, 1.5) | 確定オッズで判断 | **後知恵。確定オッズは締切後にしかわからない。楽観的な上限値として参考に表示する** |
| S4 `ev_final_haircut` | S3で、オッズを0.85倍してから判断 | 同上 | 締切直前のオッズ変動を粗く見込んだもの |
| S5 `confidence_skip` | 1着確率の最大値 ≥ α のレースだけ S1 を行う | 使わない | レースの選別 |

- 閾値(k, θ, τ, α)は valid で決め、test では固定したまま1回だけ評価する。
- 回収率はばらつきが大きいので、レースごとのブートストラップ(1,000回)で95%区間も出す。
- 券種は MVP では 3連単・2連単・単勝の3つ。
- 締切前オッズ(公式サイトから取得。Phase 2)を使う戦略・オッズの変動の分析・フォワードテストは `07_official_odds_and_settings.md` 7章(M-7・M-8)。

## 7. 予想(当日)

`python -m src.model.predict --date 2026-11-02 --variant pre --model-id latest`

出力: `data/predictions/20261102_pre.parquet`(1行=1艇。中間データで、export の入力になる)

| 列 | 内容 |
|---|---|
| race_id, entry_number | |
| model_id, variant, predicted_at | |
| score | 生のスコア |
| p_win, p_top2, p_top3 | Harvilleで計算したもの |

組合せ確率は行が多いので parquet に保存しない。export 時に `src/model/combinations.py` の関数で、p_win から計算し直す(同じ関数を使うので、結果は必ず一致する)。

`models/latest` は、採用したモデルを指すテキストファイル(model_id が1行だけ書かれたもの)とする。

## 8. 完了条件(MVP)

- B0・B1・LightGBM(pre)の valid/test の指標の比較表を `reports/model_comparison.md` に出す(手書きではなくスクリプトで生成する)
- S1〜S4 の test 期間の回収率と95%区間を `summary.json` に出す
- `predict` が当日分で動き、全レースで p_win の合計が1(誤差1e-6以内)になる
- `combinations.py` にユニットテストがある(3連単の合計が1、2連単の周辺和が単勝と一致、など)
