"""学習済みモデルの較正と Harville γ を valid だけで選び直し、新しい model_id として保存する。

CLI:
    python -m src.model.calibrate --model-id pre_20261103_9c45 [--set-latest]
    python -m src.model.calibrate --model-id preview_20261104_5c3d --variant preview --set-latest

- 木(model.txt)はそのままコピーする(再学習しない)。変わるのは meta.json の
  calibration / temperature / harville_gamma / gamma2 / gamma3 だけ。
- 選択に使うのは valid だけ(較正の方法は valid 内の2分割交差検証、γ は valid の3連単 logloss)。
  test は一切見ない。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from src.model.calibration import apply_matrix, search_gamma, select_calibration, trifecta_index, trifecta_logloss
from src.model.config import MODELS_DIR, VARIANTS, Periods
from src.model.data import clean_race_mask, features_path, filter_period, load_features, race_matrix, train_row_mask
from src.model.lgbm import harville_gamma_meta, load_model, model_dir, raw_scores, write_latest

JST = timezone(timedelta(hours=9))


def course_prior(features: pd.DataFrame, periods: Periods) -> np.ndarray:
    """学習期間のコース(枠番)別1着率。prior_shrink の縮め先。"""
    tr = filter_period(features, *periods.range_of("train"))
    tr = tr[train_row_mask(tr)]
    r = tr.groupby("entry_number")["is_win"].mean().reindex(range(1, 7)).to_numpy(float)
    return r / r.sum()


def valid_matrices(features: pd.DataFrame, booster, meta: dict):
    """valid のきれいなレースについて (S, 着順 F, 1着の列, 日付) を返す。"""
    periods = Periods(**meta["periods"])
    va = filter_period(features, *periods.range_of("valid"))
    va = va[clean_race_mask(va)].copy()
    va["score"] = raw_scores(booster, va, meta)
    codes, S = race_matrix(va, "score")
    _, F = race_matrix(va, "finish_position")
    dates = va.groupby("race_id", sort=False)["date"].first().reindex(codes).to_numpy()
    return S, F, (F == 1).argmax(axis=1), dates


def calibration_updates(booster, meta: dict, feats: pd.DataFrame, use_gamma: bool = True) -> dict:
    """valid だけで較正と γ を選び、meta に上書きする項目(calibration, temperature, harville_gamma など)を返す。"""
    periods = Periods(**meta["periods"])
    prior = course_prior(feats, periods)
    S, F, winner, dates = valid_matrices(feats, booster, meta)
    calib, cv = select_calibration(S, winner, dates, prior)
    P = apply_matrix(S, calib)
    ok, idx = trifecta_index(F)
    tri_g1 = trifecta_logloss(P[ok], idx, 1.0, 1.0)
    g2, g3, tri_best = search_gamma(P[ok], idx) if use_gamma else (1.0, 1.0, tri_g1)
    return {
        "calibration": calib,
        # 温度T を使う方法なら、その T(他の方法では参考値として元の T を残す)
        "temperature": calib["params"].get("T", meta["temperature"]),
        "harville_gamma": harville_gamma_meta(g2, g3),
        "gamma2": g2,
        "gamma3": g3,
        "calibration_selection": {
            "selected_on": "valid のみ(日付で2分割の交差検証)",
            "cv_win_logloss": cv,
            "valid_trifecta_logloss_gamma1": tri_g1,
            "valid_trifecta_logloss_selected": tri_best,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="較正と γ を valid で選び直す")
    parser.add_argument("--model-id", required=True)
    parser.add_argument("--variant", default="pre", choices=list(VARIANTS))
    parser.add_argument("--features", default=None)
    parser.add_argument("--models-dir", default=str(MODELS_DIR))
    parser.add_argument("--no-gamma", action="store_true", help="γ を探索しない(1のまま)")
    parser.add_argument("--set-latest", action="store_true", help="この variant の latest を新しいモデルにする")
    args = parser.parse_args()

    booster, meta = load_model(args.model_id, args.models_dir, args.variant)
    feats = load_features(args.features or features_path(meta["variant"]))
    upd = calibration_updates(booster, meta, feats, use_gamma=not args.no_gamma)
    calib, cv = upd["calibration"], upd["calibration_selection"]["cv_win_logloss"]
    g2, g3 = upd["gamma2"], upd["gamma3"]
    tri_g1 = upd["calibration_selection"]["valid_trifecta_logloss_gamma1"]
    tri_best = upd["calibration_selection"]["valid_trifecta_logloss_selected"]

    new = dict(meta)
    created = datetime.now(JST)
    tag = hashlib.sha256((meta["model_id"] + json.dumps(calib, sort_keys=True) + f"{g2},{g3}").encode()).hexdigest()[:4]
    new.update(upd)
    new.update(
        {
            "model_id": f"{meta['variant']}_{created:%Y%m%d}_{tag}",
            "base_model_id": meta["model_id"],
            "created_at": created.isoformat(timespec="seconds"),
        }
    )
    d = model_dir(new["model_id"], args.models_dir)
    d.mkdir(parents=True, exist_ok=True)
    shutil.copy2(model_dir(meta["model_id"], args.models_dir) / "model.txt", d / "model.txt")
    (d / "meta.json").write_text(json.dumps(new, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.set_latest:
        write_latest(new["model_id"], new["variant"], args.models_dir)

    print("交差検証の win_logloss(valid):", {k: round(v, 4) for k, v in cv.items()})
    print(f"選択: {calib['method']}  γ2={g2} γ3={g3}  valid 3連単 logloss {tri_g1:.4f} → {tri_best:.4f}")
    print(f"→ {d}{'(latest 更新)' if args.set_latest else ''}")


if __name__ == "__main__":
    main()
