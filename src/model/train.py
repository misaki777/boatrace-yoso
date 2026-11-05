"""LightGBM(1着確率)の学習。spec/03 2.1章・3章。

流れ:
1. 特徴量テーブルを読み、train / valid に分ける(時系列。シャッフルしない)
2. objective=binary で学習し、valid の binary_logloss で early stopping
3. valid の生スコアから、レース内 softmax の温度 T を多クラス logloss 最小で決める
4. 同じ期間で B1 を学習し、valid の win_logloss を比べる
5. models/{variant}/{model_id}/ に model.txt と meta.json を書く
6. LightGBM が B1 より良ければ models/latest を更新する(--no-set-latest で更新しない)

CLI:
    python -m src.model.train --variant pre [--features PATH] [--models-dir DIR]
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from src.model.baselines import B1Logistic
from src.model.config import (
    DEFAULT_PERIODS,
    EARLY_STOPPING_ROUNDS,
    LGBM_PARAMS,
    MODELS_DIR,
    NUM_BOOST_ROUND,
    SEED,
    TEMPERATURE_RANGE,
    VARIANTS,
    Periods,
)
from src.model.data import (
    clean_race_mask,
    data_hash,
    feature_lists,
    features_path,
    filter_period,
    fit_temperature,
    load_features,
    race_softmax,
    train_row_mask,
    win_logloss_rows,
)
from src.model.lgbm import harville_gamma_meta, import_lightgbm, model_dir, prepare_X, write_latest

JST = timezone(timedelta(hours=9))


def train_model(
    features: pd.DataFrame,
    variant: str = "pre",
    periods: Periods = DEFAULT_PERIODS,
    params: dict | None = None,
    num_boost_round: int = NUM_BOOST_ROUND,
    early_stopping_rounds: int = EARLY_STOPPING_ROUNDS,
    features_file: str | None = None,
) -> tuple[object, dict]:
    """学習して (booster, meta) を返す(ファイルには書かない)。"""
    lgb = import_lightgbm()
    fl = feature_lists(variant)
    feats, cats = fl["features"], fl["categorical"]
    params = {**LGBM_PARAMS, **(params or {})}

    train_all = filter_period(features, *periods.range_of("train"))
    valid_all = filter_period(features, *periods.range_of("valid"))
    train = train_all[train_row_mask(train_all)]
    valid = valid_all[train_row_mask(valid_all)]
    if train.empty or valid.empty:
        raise ValueError("train または valid の期間にデータがありません")

    X_tr, y_tr = prepare_X(train, feats, cats), train["is_win"].to_numpy().astype(int)
    X_va, y_va = prepare_X(valid, feats, cats), valid["is_win"].to_numpy().astype(int)
    dtrain = lgb.Dataset(X_tr, y_tr, categorical_feature=cats, free_raw_data=False)
    dvalid = lgb.Dataset(X_va, y_va, categorical_feature=cats, reference=dtrain, free_raw_data=False)
    booster = lgb.train(
        params,
        dtrain,
        num_boost_round=num_boost_round,
        valid_sets=[dvalid],
        valid_names=["valid"],
        callbacks=[lgb.early_stopping(early_stopping_rounds, verbose=False), lgb.log_evaluation(0)],
    )
    best_iter = int(booster.best_iteration) if booster.best_iteration > 0 else int(booster.current_iteration())

    # 温度 T: 評価と同じく「きれいなレース」(欠場・同着なし、6艇)で決める
    v_clean = valid_all[clean_race_mask(valid_all)]
    s_va = booster.predict(prepare_X(v_clean, feats, cats), raw_score=True, num_iteration=best_iter)
    rid = v_clean["race_id"].to_numpy()
    yw = v_clean["is_win"].to_numpy().astype(int)
    ll_t1 = win_logloss_rows(rid, race_softmax(rid, s_va, 1.0), yw)
    temperature, ll_t = fit_temperature(rid, s_va, yw, bounds=TEMPERATURE_RANGE)

    # B1 との比較(採用の判断に使う)
    b1 = B1Logistic().fit(train_all, valid_all)
    ll_b1 = win_logloss_rows(rid, b1.predict(v_clean), yw)

    h = data_hash(pd.concat([train, valid]), ["race_id", "entry_number", "is_win", *feats])
    created = datetime.now(JST)
    model_id = f"{variant}_{created:%Y%m%d}_{hashlib.sha256((h + json.dumps(params, sort_keys=True) + str(best_iter)).encode()).hexdigest()[:4]}"
    importance = booster.feature_importance(importance_type="gain", iteration=best_iter)

    meta = {
        "model_id": model_id,
        "variant": variant,
        "created_at": created.isoformat(timespec="seconds"),
        "train_until": periods.train_to,
        "periods": periods.to_dict(),
        "features": feats,
        "categorical": cats,
        "feature_source": fl["source"],
        "features_file": features_file,
        "params": params,
        "num_boost_round": num_boost_round,
        "early_stopping_rounds": early_stopping_rounds,
        "best_iteration": best_iter,
        "temperature": temperature,
        # Harville の γ補正(MVP は補正なし)。export が読むのは harville_gamma(null = 補正なし)。
        # gamma2/gamma3 は参照用に残す(値は harville_gamma と必ず一致させる)
        "harville_gamma": harville_gamma_meta(1.0, 1.0),
        "gamma2": 1.0,
        "gamma3": 1.0,
        "seed": SEED,
        "data_hash": h,
        "n_train_rows": int(len(train)),
        "n_valid_rows": int(len(valid)),
        "n_valid_races_clean": int(len(np.unique(rid))),
        "valid_win_logloss": ll_t,
        "valid_win_logloss_t1": ll_t1,
        "valid_win_logloss_b1": ll_b1,
        "beats_b1": bool(ll_t < ll_b1),
        "lightgbm_version": lgb.__version__,
        "feature_importance_gain": {f: float(v) for f, v in sorted(zip(feats, importance), key=lambda x: -x[1])},
    }
    return booster, meta


def save_model(booster, meta: dict, models_dir: str | Path = MODELS_DIR, set_latest: bool | None = None) -> Path:
    """model.txt と meta.json を書く。set_latest=None なら B1 に勝ったときだけ latest を更新する。"""
    d = model_dir(meta["model_id"], models_dir)
    d.mkdir(parents=True, exist_ok=True)
    booster.save_model(str(d / "model.txt"), num_iteration=meta["best_iteration"])
    (d / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    if set_latest is None:
        set_latest = meta["beats_b1"]
    if set_latest:
        write_latest(meta["model_id"], meta["variant"], models_dir)
    return d


def main() -> None:
    parser = argparse.ArgumentParser(description="LightGBM の学習")
    parser.add_argument("--variant", default="pre", choices=list(VARIANTS))
    parser.add_argument("--features", default=None, help="特徴量の parquet(省略時は data/features/features_{variant}.parquet)")
    parser.add_argument("--models-dir", default=str(MODELS_DIR))
    parser.add_argument("--num-boost-round", type=int, default=NUM_BOOST_ROUND)
    parser.add_argument("--early-stopping", type=int, default=EARLY_STOPPING_ROUNDS)
    parser.add_argument("--train-from", default=None)
    parser.add_argument("--train-to", default=None)
    parser.add_argument("--valid-from", default=None)
    parser.add_argument("--valid-to", default=None)
    parser.add_argument("--test-from", default=None)
    parser.add_argument("--test-to", default=None)
    g = parser.add_mutually_exclusive_group()
    g.add_argument("--force-latest", action="store_true", help="B1 に負けても models/latest を更新する")
    g.add_argument("--no-set-latest", action="store_true", help="models/latest を更新しない")
    args = parser.parse_args()

    overrides = {k: v for k, v in vars(args).items() if k.endswith(("_from", "_to")) and v is not None}
    periods = Periods(**{**DEFAULT_PERIODS.to_dict(), **overrides})
    path = args.features or str(features_path(args.variant))
    feats = load_features(path)
    booster, meta = train_model(
        feats, args.variant, periods,
        num_boost_round=args.num_boost_round, early_stopping_rounds=args.early_stopping, features_file=path,
    )
    set_latest = True if args.force_latest else (False if args.no_set_latest else None)
    d = save_model(booster, meta, args.models_dir, set_latest)
    print(f"model_id={meta['model_id']} best_iteration={meta['best_iteration']} T={meta['temperature']:.3f}")
    print(f"valid win_logloss: LightGBM={meta['valid_win_logloss']:.4f}(T=1: {meta['valid_win_logloss_t1']:.4f})"
          f" B1={meta['valid_win_logloss_b1']:.4f} → {'B1より良い' if meta['beats_b1'] else 'B1より悪い(latest は更新しない)'}")
    print(f"→ {d}")


if __name__ == "__main__":
    main()
