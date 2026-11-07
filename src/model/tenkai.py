"""展開予想: 決まり手6クラスの確率(spec/10 3.1、M-14)。

1行=1レース(特徴量担当 F-9 の data/features/tenkai_{variant}.parquet。予定コース順の c1_〜c6_ 列)。
LightGBM の multiclass で学習し、較正(なし / 温度T / 温度T + クラスごとの補正項)は valid だけで選ぶ。

基準:
- B0: 学習期間の決まり手の比率
- B1: 場ごとの決まり手の比率(対象日の前日までの累計。件数が少ない場は全体の比率へ縮める、α=20)
採用の条件: valid で B1 より logloss が良いこと。

CLI:
    python -m src.model.tenkai --variant pre [--set-latest]
    → models/tenkai/{variant}/{model_id}/(model.txt, meta.json)、reports/tenkai_{variant}.json と .md
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import minimize

from src.model.config import DEFAULT_PERIODS, EARLY_STOPPING_ROUNDS, FEATURES_DIR, LGBM_PARAMS, MODELS_DIR, REPORTS_DIR, SEED, Periods
from src.model.data import data_hash, filter_period
from src.model.lgbm import import_lightgbm, prepare_X
from src.model.summary import calibration_bins

JST = timezone(timedelta(hours=9))
CLASSES = ("nige", "sashi", "makuri", "makurisashi", "nuki", "megumare")  # technique_number 1〜6
PROB_COLUMNS = [f"p_{c}" for c in CLASSES]
N_CLASSES = 6
STADIUM_ALPHA = 20.0
TENKAI_VARIANTS = ("pre", "preview", "preview_v3")
_EPS = 1e-15


def feature_lists(variant: str) -> tuple[list[str], list[str]]:
    from src.features.feature_sets import tenkai_categorical_features, tenkai_features_for

    return tenkai_features_for(variant), tenkai_categorical_features(variant)


def load_tenkai(variant: str, path: str | Path | None = None) -> pd.DataFrame:
    df = pd.read_parquet(path or FEATURES_DIR / f"tenkai_{variant}.parquet")
    df["date"] = pd.to_datetime(df["date"])
    df["race_id"] = df["race_id"].astype(str)
    return df


def labeled(df: pd.DataFrame) -> pd.DataFrame:
    """学習・評価に使うレース(結果が有効で、決まり手がある)。y は 0〜5。"""
    ok = df["is_valid_result"].astype("boolean").fillna(False).astype(bool) & df["technique_number"].notna()
    d = df[ok].copy()
    d["y"] = d["technique_number"].astype(int) - 1
    return d[(d["y"] >= 0) & (d["y"] < N_CLASSES)]


def multi_logloss(P: np.ndarray, y: np.ndarray) -> float:
    return float(-np.mean(np.log(np.clip(P[np.arange(len(y)), y], _EPS, 1))))


def softmax(Z: np.ndarray) -> np.ndarray:
    Z = Z - Z.max(axis=1, keepdims=True)
    E = np.exp(Z)
    return E / E.sum(axis=1, keepdims=True)


# ---------------------------------------------------------------------------
# 基準
# ---------------------------------------------------------------------------


def baseline_overall(train: pd.DataFrame) -> np.ndarray:
    """B0: 学習期間の決まり手の比率。"""
    c = np.bincount(train["y"], minlength=N_CLASSES).astype(float) + 0.5
    return c / c.sum()


def baseline_stadium(history: pd.DataFrame, target: pd.DataFrame, prior: np.ndarray) -> np.ndarray:
    """B1: 場ごとの決まり手の比率(target の各レースの日付の前日までの累計)。全体の比率へ α で縮める。"""
    h = history[["date", "stadium_number", "y"]].copy()
    onehot = np.eye(N_CLASSES)[h["y"].to_numpy()]
    daily = pd.DataFrame(onehot, columns=range(N_CLASSES)).assign(date=h["date"].to_numpy(),
                                                                 stadium_number=h["stadium_number"].to_numpy())
    daily = daily.groupby(["stadium_number", "date"]).sum().sort_index()
    cum = daily.groupby(level=0).cumsum()
    out = np.empty((len(target), N_CLASSES))
    for i, (s, d) in enumerate(zip(target["stadium_number"].to_numpy(), target["date"].to_numpy())):
        counts = np.zeros(N_CLASSES)
        if s in cum.index.get_level_values(0):
            cs = cum.loc[s]
            prev = cs[cs.index < d]
            if len(prev):
                counts = prev.iloc[-1].to_numpy(float)
        out[i] = (counts + STADIUM_ALPHA * prior) / (counts.sum() + STADIUM_ALPHA)
    return out


# ---------------------------------------------------------------------------
# 較正
# ---------------------------------------------------------------------------


def apply_calibration(Z: np.ndarray, calib: dict) -> np.ndarray:
    """生スコア Z (n,6) → 確率。"""
    m, p = calib["method"], calib["params"]
    if m == "none":
        return softmax(Z)
    if m == "temperature":
        return softmax(Z / p["T"])
    if m == "temperature_bias":
        return softmax(Z / p["T"] + np.asarray(p["b"])[None, :])
    raise ValueError(m)


def fit_calibration(method: str, Z: np.ndarray, y: np.ndarray) -> dict:
    if method == "none":
        return {"method": "none", "params": {}}
    r = minimize(lambda x: multi_logloss(softmax(Z / np.exp(x[0])), y), [0.0], method="L-BFGS-B", bounds=[(-1.6, 1.6)])
    T = float(np.exp(r.x[0]))
    if method == "temperature":
        return {"method": method, "params": {"T": T}}
    r = minimize(lambda x: multi_logloss(softmax(Z / np.exp(x[0]) + np.r_[0.0, x[1:]][None, :]), y),
                 np.r_[np.log(T), np.zeros(Z.shape[1] - 1)], method="L-BFGS-B")
    return {"method": method, "params": {"T": float(np.exp(r.x[0])), "b": [0.0, *map(float, r.x[1:])]}}


def select_calibration(Z: np.ndarray, y: np.ndarray, dates: np.ndarray) -> tuple[dict, dict]:
    """valid 内を日付で2分割した交差検証で選び、valid 全体で当て直す。"""
    d = dates.astype("datetime64[D]").astype(np.int64)
    first = d <= np.median(d)
    cv = {}
    for m in ("none", "temperature", "temperature_bias"):
        tot = 0.0
        for a, b in ((first, ~first), (~first, first)):
            tot += multi_logloss(apply_calibration(Z[b], fit_calibration(m, Z[a], y[a])), y[b]) * b.sum()
        cv[m] = tot / len(y)
    best = min(cv, key=cv.get)
    return fit_calibration(best, Z, y), cv


# ---------------------------------------------------------------------------
# 学習・評価
# ---------------------------------------------------------------------------


def raw_scores(booster, df: pd.DataFrame, meta: dict) -> np.ndarray:
    X = prepare_X(df, meta["features"], meta["categorical"])
    return booster.predict(X, raw_score=True, num_iteration=meta.get("best_iteration"))


def predict_proba(booster, df: pd.DataFrame, meta: dict) -> np.ndarray:
    return apply_calibration(raw_scores(booster, df, meta), meta["calibration"])


def metrics(P: np.ndarray, y: np.ndarray) -> dict:
    """logloss・正解率・クラスごとの較正(10帯)・クラスごとの予測平均と実際の比率。"""
    Y = np.eye(N_CLASSES)[y]
    return {
        "races": int(len(y)),
        "logloss": multi_logloss(P, y),
        "accuracy": float((P.argmax(axis=1) == y).mean()),
        "brier": float(((P - Y) ** 2).sum(axis=1).mean()),
        "class_mean": {c: {"pred": float(P[:, k].mean()), "actual": float(Y[:, k].mean())} for k, c in enumerate(CLASSES)},
        "calibration": {c: calibration_bins(P[:, k], Y[:, k]) for k, c in enumerate(CLASSES)},
    }


def train(variant: str, periods: Periods = DEFAULT_PERIODS, path=None, num_boost_round: int = 5000,
          early_stopping: int = EARLY_STOPPING_ROUNDS, params: dict | None = None) -> tuple[object, dict, dict]:
    """学習・較正・評価をして (booster, meta, report) を返す。"""
    lgb = import_lightgbm()
    feats, cats = feature_lists(variant)
    df = labeled(load_tenkai(variant, path))
    tr = filter_period(df, *periods.range_of("train"))
    va = filter_period(df, *periods.range_of("valid"))
    te = filter_period(df, *periods.range_of("test"))
    p = {**LGBM_PARAMS, "objective": "multiclass", "num_class": N_CLASSES, "metric": "multi_logloss",
         "min_data_in_leaf": 50, **(params or {})}
    dtr = lgb.Dataset(prepare_X(tr, feats, cats), tr["y"].to_numpy(), categorical_feature=cats, free_raw_data=False)
    dva = lgb.Dataset(prepare_X(va, feats, cats), va["y"].to_numpy(), categorical_feature=cats, reference=dtr,
                      free_raw_data=False)
    booster = lgb.train(p, dtr, num_boost_round=num_boost_round, valid_sets=[dva], valid_names=["valid"],
                        callbacks=[lgb.early_stopping(early_stopping, verbose=False), lgb.log_evaluation(0)])
    best = int(booster.best_iteration) or int(booster.current_iteration())

    meta = {"features": feats, "categorical": cats, "best_iteration": best}
    Zva = raw_scores(booster, va, meta)
    calib, cv = select_calibration(Zva, va["y"].to_numpy(), va["date"].to_numpy())

    created = datetime.now(JST)
    h = data_hash(tr, ["race_id", "y", *feats])
    model_id = f"tenkai_{variant}_{created:%Y%m%d}_{hashlib.sha256((h + json.dumps(calib, sort_keys=True)).encode()).hexdigest()[:4]}"
    imp = booster.feature_importance("gain", iteration=best)
    meta.update({
        "model_id": model_id, "kind": "tenkai", "variant": variant, "classes": list(CLASSES),
        "created_at": created.isoformat(timespec="seconds"), "train_until": periods.train_to,
        "periods": periods.to_dict(), "params": p, "seed": SEED, "data_hash": h, "calibration": calib,
        "calibration_selection": {"selected_on": "valid のみ(日付で2分割の交差検証)", "cv_logloss": cv},
        "n_train": int(len(tr)), "n_valid": int(len(va)), "lightgbm_version": lgb.__version__,
        "feature_importance_gain": {f: float(v) for f, v in sorted(zip(feats, imp), key=lambda x: -x[1])},
    })

    # 評価(valid で選んだものを test に1回当てるだけ)
    prior = baseline_overall(tr)
    history = df[df["date"] < pd.Timestamp(periods.test_to) + pd.Timedelta(days=1)]
    report = {"model_id": model_id, "variant": variant, "cv_logloss": cv, "calibration_method": calib["method"],
              "periods": {}}
    for name, part in (("valid", va), ("test", te)):
        y = part["y"].to_numpy()
        P = predict_proba(booster, part, meta)
        b0 = np.tile(prior, (len(part), 1))
        b1 = baseline_stadium(history, part, prior)
        report["periods"][name] = {"lgbm": metrics(P, y), "B0": metrics(b0, y), "B1": metrics(b1, y)}
    v = report["periods"]["valid"]
    meta["valid_logloss"] = v["lgbm"]["logloss"]
    meta["valid_logloss_b1"] = v["B1"]["logloss"]
    meta["beats_b1"] = bool(v["lgbm"]["logloss"] < v["B1"]["logloss"])
    return booster, meta, report


def model_root(variant: str, models_dir: str | Path = MODELS_DIR) -> Path:
    return Path(models_dir) / "tenkai" / variant


def save(booster, meta: dict, models_dir: str | Path = MODELS_DIR, set_latest: bool = False) -> Path:
    d = model_root(meta["variant"], models_dir) / meta["model_id"]
    d.mkdir(parents=True, exist_ok=True)
    booster.save_model(str(d / "model.txt"), num_iteration=meta["best_iteration"])
    (d / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    if set_latest:
        latest = model_root(meta["variant"], models_dir) / "latest"
        tmp = latest.with_suffix(".tmp")
        tmp.write_text(meta["model_id"] + "\n", encoding="utf-8")
        tmp.replace(latest)
    return d


def load(model_id: str, variant: str, models_dir: str | Path = MODELS_DIR):
    lgb = import_lightgbm()
    root = model_root(variant, models_dir)
    if model_id == "latest":
        model_id = (root / "latest").read_text(encoding="utf-8").strip()
    meta = json.loads((root / model_id / "meta.json").read_text(encoding="utf-8"))
    return lgb.Booster(model_file=str(root / model_id / "model.txt")), meta


# ---------------------------------------------------------------------------
# 本体モデルとの整合(逃げの確率 vs 予定1コースの艇の1着確率)
# ---------------------------------------------------------------------------


def consistency(tenkai_df: pd.DataFrame, P: np.ndarray, main_model: str, main_variant: str, period_part) -> dict:
    """p_nige と、本体モデルの「予定1コースの艇の p_win」の差の分布。

    逃げ = 1コースの艇が1着、なので理屈の上では p_win(1コース) ≥ p_nige(差は抜き・恵まれで1コースが勝つ分)。
    """
    from src.model.data import features_path, load_features
    from src.model.lgbm import load_model
    from src.model.report import predict_p

    booster, meta = load_model(main_model, variant=main_variant)
    feats = period_part(load_features(features_path(meta["variant"])))
    p = feats[["race_id", "entry_number"]].assign(p_win=predict_p(feats, booster, meta))
    t = tenkai_df[["race_id", "c1_entry_number"]].assign(p_nige=P[:, 0], y=tenkai_df["y"].to_numpy())
    m = t.merge(p, left_on=["race_id", "c1_entry_number"], right_on=["race_id", "entry_number"], how="inner")
    diff = (m["p_win"] - m["p_nige"]).to_numpy()
    c1_won_by_nige = (m["y"] == 0).mean()
    return {
        "races": int(len(m)), "main_model": meta["model_id"],
        "mean_p_win_c1": float(m["p_win"].mean()), "mean_p_nige": float(m["p_nige"].mean()),
        "actual_nige_rate": float(c1_won_by_nige),
        "diff_p_win_minus_p_nige": {q: float(np.quantile(diff, v)) for q, v in
                                    (("p05", 0.05), ("p25", 0.25), ("median", 0.5), ("p75", 0.75), ("p95", 0.95))},
        "abs_diff_over_0.10": float((np.abs(diff) > 0.10).mean()),
        "corr": float(np.corrcoef(m["p_win"], m["p_nige"])[0, 1]),
    }


def render(report: dict) -> str:
    L = [f"# 展開予想(決まり手)モデル {report['model_id']}", "",
         "`python -m src.model.tenkai` が自動で生成。較正は valid だけで選び、test は評価だけ。", "",
         f"- 較正: {report['calibration_method']}(valid 交差検証 logloss: "
         + ", ".join(f"{k} {v:.4f}" for k, v in report["cv_logloss"].items()) + ")", ""]
    for period, r in report["periods"].items():
        L += [f"## {period}", "", "| model | races | logloss | 正解率 | brier |", "|---|---|---|---|---|"]
        for name in ("B0", "B1", "lgbm"):
            m = r[name]
            L.append(f"| {name} | {m['races']} | {m['logloss']:.4f} | {m['accuracy']:.3f} | {m['brier']:.4f} |")
        L += ["", "| 決まり手 | 予測平均 | 実際 |", "|---|---|---|"]
        for c, v in r["lgbm"]["class_mean"].items():
            L.append(f"| {c} | {v['pred']:.3f} | {v['actual']:.3f} |")
        L += ["", "逃げの較正(帯: 予測 / 実際 (n)): " + "、".join(
            f"{b['bin']}%: {b['pred_mean']:.3f}/{b['actual']:.3f}({b['n']})" for b in r["lgbm"]["calibration"]["nige"]), ""]
    if report.get("consistency"):
        c = report["consistency"]
        L += ["## 本体モデルとの整合(test)", "",
              f"- 本体 {c['main_model']}: 予定1コースの艇の p_win 平均 {c['mean_p_win_c1']:.3f}、展開の p_nige 平均 {c['mean_p_nige']:.3f}、"
              f"実際の逃げの率 {c['actual_nige_rate']:.3f}",
              "- 差(p_win − p_nige)の分位: " + "、".join(f"{k} {v:+.3f}" for k, v in c["diff_p_win_minus_p_nige"].items()),
              f"- |差|>0.10 のレース: {c['abs_diff_over_0.10']:.1%}、相関 {c['corr']:.3f}", ""]
    return "\n".join(L)


def main() -> None:
    parser = argparse.ArgumentParser(description="展開予想(決まり手)モデルの学習と評価")
    parser.add_argument("--variant", default="pre", choices=list(TENKAI_VARIANTS))
    parser.add_argument("--features", default=None)
    parser.add_argument("--models-dir", default=str(MODELS_DIR))
    parser.add_argument("--set-latest", action="store_true", help="valid で B1 に勝ったときだけ latest を更新する")
    parser.add_argument("--train-from", default=None, help="学習の開始日(既定は config の期間)")
    args = parser.parse_args()

    periods = DEFAULT_PERIODS
    if args.train_from:
        periods = Periods(**{**DEFAULT_PERIODS.to_dict(), "train_from": args.train_from})
    booster, meta, report = train(args.variant, periods, path=args.features)
    periods = Periods(**meta["periods"])
    te = filter_period(labeled(load_tenkai(args.variant, args.features)), *periods.range_of("test"))
    P = predict_proba(booster, te, meta)
    main_variant = "preview" if args.variant.startswith("preview") else "pre"
    report["consistency"] = consistency(te, P, "latest", main_variant,
                                        lambda f: filter_period(f, *periods.range_of("test")))
    d = save(booster, meta, args.models_dir, set_latest=args.set_latest and meta["beats_b1"])
    tag = args.variant + (f"_from{args.train_from}" if args.train_from else "")
    (REPORTS_DIR / f"tenkai_{tag}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (REPORTS_DIR / f"tenkai_{tag}.md").write_text(render(report), encoding="utf-8")
    for period, r in report["periods"].items():
        print(f"[{period}] logloss: B0={r['B0']['logloss']:.4f} B1={r['B1']['logloss']:.4f} LightGBM={r['lgbm']['logloss']:.4f}")
    print(f"較正={meta['calibration']['method']} beats_b1(valid)={meta['beats_b1']} → {d}")


if __name__ == "__main__":
    main()
