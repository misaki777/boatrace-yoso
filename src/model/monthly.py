"""毎月の作り直し(spec/10 2.5、M-13)。GitHub Actions の monthly.yml(CI-3)から呼ぶ。

CLI:
    python -m src.model.monthly [--month 2026-12] [--targets pre preview tenkai_pre tenkai_preview]
    python -m src.model.monthly --approve <candidate_id>      # 承認制の期間に、候補へ切り替える
    python -m src.model.monthly --list-pending                 # 承認待ちの候補を表示

手順(対象ごと)。実行した月を M、前月を M−1 とする:
 1. 選択用モデル: データ開始(+warm-up 90日)〜 M−2 の末日で学習し、M−1 を valid にして early stopping。
    較正と Harville の γ も M−1 だけで選ぶ(本体)。展開予想は較正だけ。
 2. 候補: 同じ設定で M−1 の末日までのデータで学び直す(木の数 = 選択用の 1.1 倍)。較正と γ は 1 のものを引き継ぐ。
 3. 基準(すべて満たしたら切り替え。指標は M−1 で、選択用モデルと今の latest を比べる):
    (a) 1着の logloss(展開予想は多クラス logloss)が latest より 0.002 以上良い
    (b) 3連単 logloss が悪化しない(展開予想は対象外 = 満たす扱い)
    (c) 較正の ECE(10帯の |予測−実際| の件数加重平均)が悪化しない
    (d) M−1 の実運用のレース(確定版の予想があるレース)で、候補の logloss が確定版より悪化しない。
        確定版が無ければ判定できないので「満たす」扱いにし、理由に書く
    (e) 学習の健全性(行数、特徴量の欠損率、予測の合計=1)
 4. 承認制: models/history.json にある実行月が APPROVAL_RUNS(2)か月分に達するまでは、基準を満たしても
    切り替えず「承認待ち」として記録する。--approve で切り替える。以降は自動で切り替える。
 5. 確定版の予想は書き換えない。切り替えは翌日のジョブから効く。

記録: models/history.json(1行=対象ごとの判定)。
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from src.model.config import MODELS_DIR, PREDICTIONS_DIR, Periods
from src.model.data import features_path, filter_period, load_features
from src.model.summary import calibration_bins

JST = timezone(timedelta(hours=9))
APPROVAL_RUNS = 2  # 最初の2か月は承認制(利用者の決定)
WIN_IMPROVE = 0.002
WARMUP_DAYS = 90
MAIN_TARGETS = ("pre", "preview")
TENKAI_TARGETS = ("tenkai_pre", "tenkai_preview")
MIN_TRAIN_RACES = 1000
MAX_FEATURE_NAN = 0.5  # 1列でも欠損率がこれを超えたら健全でない


# ---------------------------------------------------------------------------
# 期間
# ---------------------------------------------------------------------------


def month_periods(month: str, data_start: str) -> tuple[Periods, Periods]:
    """(選択用, 候補の学び直し用) の期間。test は置かないので valid と同じにしておく。"""
    m = pd.Period(month, "M")
    m1, m2 = m - 1, m - 2
    train_from = (pd.Timestamp(data_start) + pd.Timedelta(days=WARMUP_DAYS)).strftime("%Y-%m-%d")
    v_from, v_to = m1.start_time.strftime("%Y-%m-%d"), m1.end_time.strftime("%Y-%m-%d")
    sel = Periods(warmup_from=data_start, train_from=train_from, train_to=m2.end_time.strftime("%Y-%m-%d"),
                  valid_from=v_from, valid_to=v_to, test_from=v_from, test_to=v_to)
    refit = Periods(warmup_from=data_start, train_from=train_from, train_to=v_to,
                    valid_from=v_from, valid_to=v_to, test_from=v_from, test_to=v_to)
    return sel, refit


# ---------------------------------------------------------------------------
# 指標
# ---------------------------------------------------------------------------


def ece(bins: list[dict]) -> float | None:
    """10帯の |予測の平均 − 実際| の件数加重平均。"""
    n = sum(b["n"] for b in bins)
    return sum(b["n"] * abs(b["pred_mean"] - b["actual"]) for b in bins) / n if n else None


def main_metrics(booster, meta: dict, part: pd.DataFrame) -> dict:
    """本体モデルの M−1 での指標(win_logloss, trifecta_logloss, ece)。"""
    from src.model.evaluate import compute_metrics
    from src.model.lgbm import gammas_from_meta
    from src.model.report import predict_p
    from src.model.summary import calibration_from_predictions

    p = part[["race_id", "stadium_number", "entry_number", "finish_position", "is_valid_result", "is_absent"]].assign(
        p_win=predict_p(part, booster, meta))
    m = compute_metrics(p, "p_win", *gammas_from_meta(meta))
    return {"win_logloss": m.get("win_logloss"), "trifecta_logloss": m.get("trifecta_logloss"),
            "ece": ece(calibration_from_predictions(p)), "races": m.get("races")}


def tenkai_metrics(booster, meta: dict, part: pd.DataFrame) -> dict:
    from src.model.tenkai import CLASSES, multi_logloss, predict_proba

    y = part["y"].to_numpy()
    P = predict_proba(booster, part, meta)
    Y = np.eye(len(CLASSES))[y]
    bins = [b for k in range(len(CLASSES)) for b in calibration_bins(P[:, k], Y[:, k])]
    return {"win_logloss": multi_logloss(P, y), "trifecta_logloss": None, "ece": ece(bins), "races": int(len(y))}


def locked_dates(month1: pd.Period, variant_file: str, pred_dir: Path) -> list[Path]:
    """M−1 の確定版の予想ファイル。"""
    out = []
    for d in pd.date_range(month1.start_time, month1.end_time, freq="D"):
        f = pred_dir / f"{d:%Y%m%d}_{variant_file}.parquet"
        if f.exists():
            out.append(f)
    return out


def live_main(booster, meta: dict, part: pd.DataFrame, files: list[Path], slot: str) -> dict | None:
    """確定版の予想があるレースで、確定版と候補の win_logloss を比べる。確定版が無ければ None。"""
    from src.model.data import clean_race_mask, race_matrix
    from src.model.report import predict_p

    if not files:
        return None
    locked = pd.concat([pd.read_parquet(f, columns=["race_id", "entry_number", "p_win", "variant"]) for f in files])
    locked = locked[locked["variant"] == slot]  # preview のファイルで pre へフォールバックした行は除く
    races = set(locked["race_id"].astype(str))
    d = part[part["race_id"].astype(str).isin(races)]
    d = d[clean_race_mask(d)]
    if d.empty:
        return None
    cand = d[["race_id", "entry_number", "finish_position"]].assign(p_cand=predict_p(d, booster, meta))
    cand["race_id"] = cand["race_id"].astype(str)
    locked["race_id"] = locked["race_id"].astype(str)
    m = cand.merge(locked[["race_id", "entry_number", "p_win"]], on=["race_id", "entry_number"])
    _, F = race_matrix(m, "finish_position")
    out = {"races": int(len(F))}
    w = (F == 1).argmax(axis=1)
    for col, key in (("p_cand", "candidate"), ("p_win", "locked")):
        _, P = race_matrix(m, col)
        out[key] = float(-np.mean(np.log(np.clip(P[np.arange(len(P)), w], 1e-15, 1))))
    return out


def live_tenkai(booster, meta: dict, part: pd.DataFrame, files: list[Path]) -> dict | None:
    from src.model.tenkai import PROB_COLUMNS, multi_logloss, predict_proba

    if not files:
        return None
    locked = pd.concat([pd.read_parquet(f) for f in files])
    locked["race_id"] = locked["race_id"].astype(str)
    d = part[part["race_id"].astype(str).isin(set(locked["race_id"]))]
    if d.empty:
        return None
    L = locked.set_index("race_id").loc[d["race_id"].astype(str), PROB_COLUMNS].to_numpy()
    y = d["y"].to_numpy()
    return {"races": int(len(d)), "candidate": multi_logloss(predict_proba(booster, d, meta), y),
            "locked": multi_logloss(L, y)}


def health_check(train_part: pd.DataFrame, features: list[str], P_sum: np.ndarray, min_races: int = MIN_TRAIN_RACES) -> dict:
    nan = train_part[features].isna().mean()
    races = train_part["race_id"].nunique()
    checks = {
        "train_races": int(races),
        "train_races_ok": bool(races >= min_races),
        "max_feature_nan": float(nan.max()),
        "max_feature_nan_column": str(nan.idxmax()),
        "feature_nan_ok": bool(nan.max() <= MAX_FEATURE_NAN),
        "prob_sum_ok": bool(np.allclose(P_sum, 1, atol=1e-6)),
    }
    checks["ok"] = checks["train_races_ok"] and checks["feature_nan_ok"] and checks["prob_sum_ok"]
    return checks


def judge(cand: dict, latest: dict | None, live: dict | None, health: dict, has_trifecta: bool) -> tuple[dict, list[str]]:
    """基準 a〜e の判定と理由。latest が無ければ a〜c は満たす扱い(初回)。"""
    res, why = {}, []
    if latest is None:
        res.update({"a": True, "b": True, "c": True})
        why.append("比べる latest が無い(初回)")
    else:
        res["a"] = cand["win_logloss"] <= latest["win_logloss"] - WIN_IMPROVE
        if has_trifecta:
            res["b"] = cand["trifecta_logloss"] <= latest["trifecta_logloss"] + 1e-12
        else:
            res["b"] = True
            why.append("b: 展開予想は3連単の判定なし")
        res["c"] = cand["ece"] <= latest["ece"] + 1e-12
    if live is None:
        res["d"] = True
        why.append("d: M−1 の確定版の予想が無いため判定できず(満たす扱い)")
    else:
        res["d"] = live["candidate"] <= live["locked"] + 1e-12
    res["e"] = bool(health["ok"])
    for k, label in (("a", f"1着 logloss が {WIN_IMPROVE} 以上良い"), ("b", "3連単 logloss が悪化しない"),
                     ("c", "較正(ECE)が悪化しない"), ("d", "前月の実運用より悪くない"), ("e", "学習の健全性")):
        if not res[k]:
            why.append(f"{k} を満たさない: {label}")
    return {k: bool(v) for k, v in res.items()}, why


# ---------------------------------------------------------------------------
# 対象ごとの処理
# ---------------------------------------------------------------------------


def run_main_target(slot: str, month: str, models_dir: Path, pred_dir: Path, feature_variant: str | None = None) -> dict:
    """本体(pre / preview)。feature_variant を指定すると、その特徴量(例 pre_v2)で候補を作る。"""
    from src.model.calibrate import calibration_updates
    from src.model.config import is_preview_variant
    from src.model.data import train_row_mask
    from src.model.lgbm import load_model, model_dir, raw_scores
    from src.model.train import save_model, train_model

    fv = feature_variant or slot
    feats = load_features(features_path(fv))
    data_start = feats["date"].min().strftime("%Y-%m-%d")
    sel_p, refit_p = month_periods(month, data_start)
    m1 = pd.Period(month, "M") - 1
    t0 = time.time()

    # 1. 選択用
    b_sel, meta_sel = train_model(feats, fv, sel_p)
    meta_sel.update(calibration_updates(b_sel, meta_sel, feats))
    valid = filter_period(feats, *sel_p.range_of("valid"))
    cand_m = main_metrics(b_sel, meta_sel, valid)

    # 2. 学び直し(木の数は 1.1 倍、early stopping はしない)
    rounds = max(1, int(round(meta_sel["best_iteration"] * 1.1)))
    b_ref, meta_ref = train_model(feats, fv, refit_p, num_boost_round=rounds, early_stopping_rounds=rounds + 1)
    meta_ref["best_iteration"] = int(b_ref.current_iteration())
    for k in ("calibration", "temperature", "harville_gamma", "gamma2", "gamma3", "calibration_selection"):
        meta_ref[k] = meta_sel[k]
    meta_ref.update({"selection_model": {"best_iteration": meta_sel["best_iteration"], "periods": sel_p.to_dict()},
                     "monthly": {"month": month, "train_to": refit_p.train_to, "valid": str(m1)}})
    # 予測の合計=1 の検査(M−1 の数レース)
    from src.model.calibration import apply_rows, calibration_from_meta

    smp = valid.head(600)
    P = apply_rows(smp["race_id"].to_numpy(), smp["entry_number"].to_numpy(), raw_scores(b_ref, smp, meta_ref),
                   calibration_from_meta(meta_ref))
    psum = pd.Series(P).groupby(smp["race_id"].to_numpy()).sum().to_numpy()
    tr = filter_period(feats, *refit_p.range_of("train"))
    health = health_check(tr[train_row_mask(tr)], meta_ref["features"], psum)
    save_model(b_ref, meta_ref, models_dir, set_latest=False)

    # 3. latest と前月の実運用
    latest_m, latest_id = None, None
    try:
        b_lat, meta_lat = load_model("latest", models_dir, slot)
        latest_id = meta_lat["model_id"]
        lat_feats = feats if meta_lat["variant"] == fv else load_features(features_path(meta_lat["variant"]))
        latest_m = main_metrics(b_lat, meta_lat, filter_period(lat_feats, *sel_p.range_of("valid")))
    except FileNotFoundError:
        pass
    files = locked_dates(m1, slot, pred_dir)
    live = live_main(b_sel, meta_sel, valid, files, slot)
    has_tri = True
    criteria, why = judge(cand_m, latest_m, live, health, has_tri)
    note = None
    if fv != slot:
        note = f"特徴量 {fv} の候補。切り替えるには毎日のジョブを --variant {fv} にする必要がある"
    return _record(slot, meta_ref["model_id"], latest_id, month, refit_p, m1, cand_m, latest_m, live, health,
                   criteria, why, time.time() - t0, note)


def run_tenkai_target(slot: str, month: str, models_dir: Path, pred_dir: Path) -> dict:
    from src.model import tenkai as tk

    variant = slot.replace("tenkai_", "")
    df = tk.labeled(tk.load_tenkai(variant))
    data_start = df["date"].min().strftime("%Y-%m-%d")
    sel_p, refit_p = month_periods(month, data_start)
    m1 = pd.Period(month, "M") - 1
    t0 = time.time()
    b_sel, meta_sel, _ = tk.train(variant, sel_p)
    valid = filter_period(df, *sel_p.range_of("valid"))
    cand_m = tenkai_metrics(b_sel, meta_sel, valid)
    rounds = max(1, int(round(meta_sel["best_iteration"] * 1.1)))
    b_ref, meta_ref, _ = tk.train(variant, refit_p, num_boost_round=rounds, early_stopping=rounds + 1)
    meta_ref["best_iteration"] = int(b_ref.current_iteration())
    meta_ref["calibration"] = meta_sel["calibration"]
    meta_ref["calibration_selection"] = meta_sel["calibration_selection"]
    meta_ref["monthly"] = {"month": month, "train_to": refit_p.train_to, "valid": str(m1)}
    P = tk.predict_proba(b_ref, valid.head(300), meta_ref)
    tr = filter_period(df, *refit_p.range_of("train"))
    health = health_check(tr, meta_ref["features"], P.sum(axis=1))
    tk.save(b_ref, meta_ref, models_dir, set_latest=False)
    latest_m, latest_id = None, None
    try:
        b_lat, meta_lat = tk.load("latest", variant, models_dir)
        latest_id = meta_lat["model_id"]
        latest_m = tenkai_metrics(b_lat, meta_lat, valid)
    except FileNotFoundError:
        pass
    live = live_tenkai(b_sel, meta_sel, valid, locked_dates(m1, f"{variant}_tenkai", pred_dir))
    criteria, why = judge(cand_m, latest_m, live, health, has_trifecta=False)
    return _record(slot, meta_ref["model_id"], latest_id, month, refit_p, m1, cand_m, latest_m, live, health,
                   criteria, why, time.time() - t0, None)


def _record(slot, cand_id, latest_id, month, refit_p, m1, cand_m, latest_m, live, health, criteria, why, secs, note):
    metrics = {}
    for k in ("win_logloss", "trifecta_logloss", "ece"):
        metrics[k] = {"candidate": cand_m.get(k), "latest": latest_m.get(k) if latest_m else None}
    rec = {
        "date": datetime.now(JST).strftime("%Y-%m-%d"), "run_month": month, "variant": slot,
        "candidate": cand_id, "latest_before": latest_id, "train_to": refit_p.train_to, "valid": str(m1),
        "metrics": metrics, "valid_races": cand_m.get("races"), "live": live, "health": health,
        "criteria": criteria, "all_criteria_met": all(criteria.values()),
        "adopted": False, "pending_approval": False, "reason": "", "seconds": round(secs, 1),
    }
    if note:
        rec["note"] = note
    rec["_why"] = why
    return rec


# ---------------------------------------------------------------------------
# 履歴と切り替え
# ---------------------------------------------------------------------------


def history_path(models_dir: Path) -> Path:
    return Path(models_dir) / "history.json"


def load_history(models_dir: Path) -> list[dict]:
    p = history_path(models_dir)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []


def save_history(models_dir: Path, hist: list[dict]) -> None:
    p = history_path(models_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(hist, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


def approval_required(hist: list[dict], month: str, approval_runs: int = APPROVAL_RUNS) -> bool:
    """この月の実行が承認制の期間か(過去に記録のある実行月が approval_runs 未満なら承認制)。"""
    months = {h["run_month"] for h in hist if h.get("run_month") and h["run_month"] != month}
    return len(months) < approval_runs


def switch_latest(slot: str, model_id: str, models_dir: Path) -> None:
    if slot.startswith("tenkai_"):
        root = Path(models_dir) / "tenkai" / slot.replace("tenkai_", "")
        tmp = root / "latest.tmp"
        tmp.write_text(model_id + "\n", encoding="utf-8")
        tmp.replace(root / "latest")
    else:
        from src.model.lgbm import write_latest

        write_latest(model_id, slot, models_dir)


def decide(rec: dict, approval: bool, models_dir: Path) -> dict:
    """基準と承認制から採否を決め、自動の期間なら切り替える。"""
    why = rec.pop("_why", [])
    if not rec["all_criteria_met"]:
        rec["reason"] = "切り替えない。" + " / ".join(why)
    elif approval:
        rec["pending_approval"] = True
        rec["reason"] = "基準 a〜e を満たした。承認制の期間のため、承認待ち(--approve で切り替え)" + (
            "。" + " / ".join(why) if why else "")
    else:
        switch_latest(rec["variant"], rec["candidate"], models_dir)
        rec["adopted"] = True
        rec["reason"] = "基準 a〜e を満たしたので切り替えた" + ("。" + " / ".join(why) if why else "")
    return rec


def approve(candidate_id: str, models_dir: Path) -> dict:
    """承認待ちの候補を latest にする。"""
    hist = load_history(models_dir)
    for h in reversed(hist):
        if h["candidate"] == candidate_id:
            if not h.get("pending_approval"):
                raise ValueError(f"{candidate_id} は承認待ちではありません(adopted={h.get('adopted')})")
            switch_latest(h["variant"], candidate_id, models_dir)
            h.update({"adopted": True, "pending_approval": False,
                      "approved_at": datetime.now(JST).isoformat(timespec="seconds"),
                      "reason": h["reason"] + "。利用者が承認して切り替えた"})
            save_history(models_dir, hist)
            return h
    raise KeyError(f"history.json に {candidate_id} がありません")


def main() -> None:
    parser = argparse.ArgumentParser(description="毎月の作り直し")
    parser.add_argument("--month", default=None, help="実行月 YYYY-MM(既定は今月 JST)。M−1 が valid になる")
    parser.add_argument("--targets", nargs="+", default=[*MAIN_TARGETS, *TENKAI_TARGETS])
    parser.add_argument("--with-v2", action="store_true", help="本体で v2 特徴量の候補も作り、valid で良い方を記録する")
    parser.add_argument("--models-dir", default=str(MODELS_DIR))
    parser.add_argument("--predictions-dir", default=str(PREDICTIONS_DIR))
    parser.add_argument("--approve", default=None, metavar="CANDIDATE_ID")
    parser.add_argument("--list-pending", action="store_true")
    args = parser.parse_args()
    models_dir = Path(args.models_dir)

    if args.approve:
        h = approve(args.approve, models_dir)
        print(f"{h['variant']}: {h['candidate']} に切り替えました")
        return
    if args.list_pending:
        for h in load_history(models_dir):
            if h.get("pending_approval"):
                print(f"{h['date']} {h['variant']} {h['candidate']}  {json.dumps(h['metrics'], ensure_ascii=False)}")
        return

    month = args.month or datetime.now(JST).strftime("%Y-%m")
    hist = load_history(models_dir)
    approval = approval_required(hist, month)
    pred_dir = Path(args.predictions_dir)
    t_all = time.time()
    for slot in args.targets:
        if slot in TENKAI_TARGETS:
            rec = run_tenkai_target(slot, month, models_dir, pred_dir)
        else:
            rec = run_main_target(slot, month, models_dir, pred_dir)
            if args.with_v2:
                rec2 = run_main_target(slot, month, models_dir, pred_dir, feature_variant=f"{slot}_v2")
                if rec2["metrics"]["win_logloss"]["candidate"] < rec["metrics"]["win_logloss"]["candidate"]:
                    rec2.setdefault("note", "")
                    rec2["note"] += f"。v1 候補 {rec['candidate']} より valid が良い"
                    rec2["all_criteria_met"] = False  # v2 への切り替えは毎日のジョブの変更が要るので自動では行わない
                    rec2["_why"].append("v2 は毎日のジョブの変更が必要なため自動では切り替えない")
                    hist.append(decide(rec2, approval, models_dir))
        rec = decide(rec, approval, models_dir)
        hist.append(rec)
        save_history(models_dir, hist)
        print(f"[{slot}] 候補 {rec['candidate']} 基準 {rec['criteria']} → {rec['reason']}({rec['seconds']:.0f}秒)")
    print(f"合計 {time.time() - t_all:.0f}秒、承認制の期間: {approval} → {history_path(models_dir)}")


if __name__ == "__main__":
    main()
