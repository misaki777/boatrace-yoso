"""踏み込んだ展開予想(利用者の決定)。A 誰の決まり手か、B スタート隊形、C 展開の一言。

入力は展開予想と同じ 1行=1レースの表(data/features/tenkai_{variant}.parquet。予定コース順の c1_〜c6_ 列。
c{k}_entry_number が予定 k コースの艇の枠番。前日版は枠番どおり、直前版は展示の進入)。

A. 誰の決まり手か(多クラス): 「1着の艇の予定コース × 決まり手」。件数の少ない組合せは other にまとめる
   (学習期間で 0.8% 未満。抜き・恵まれも other)。クラスの一覧は学習時に決めて meta に保存する。
B. スタート隊形(実際の ST = rs_start_timing。フライングの負の値もそのまま「速い」とみなす):
   - top: どの予定コースの艇がトップスタートか(6クラス。同タイムで1位が2艇以上のレースは学習・評価から外す)
   - faster_k(k=2〜6): 予定 k コースの艇が、予定 k−1 コースの艇より ST が速いか(二値。同タイムは外す)。
     faster_4 が「カド(4コース)が3コースより先に出る」確率。
C. 展開の一言: A と B から決まった型の短い文を作る(文章生成は使わない。「最適」「必ず」は使わない)。

評価: 学習は train(既定 2016-10-30〜2026-08-31)、較正は valid(9月)、10月で評価。基準は全体の比率(B0)と
場ごとの比率(B1、前日までの累計)。A は既存の展開予想(決まり手)と本体の1着確率との整合も見る。

CLI:
    python -m src.model.tenkai2 --variant pre|preview [--set-latest]      # 学習・評価(reports/tenkai2_{variant}.md)
    python -m src.model.tenkai2 --predict --date 2026-11-07 --variant pre [--force]
    python -m src.model.tenkai2 --predict --date 2026-11-07 --variant preview --per-race-lock [--now ...]
    → data/predictions/YYYYMMDD_{variant}_tenkai_detail.parquet(1行=1レース。列は OUTPUT の説明を参照)
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from src.model import tenkai as tk
from src.model.config import (
    DEFAULT_PERIODS,
    EARLY_STOPPING_ROUNDS,
    FEATURES_DIR,
    LGBM_PARAMS,
    MODELS_DIR,
    PREDICTIONS_DIR,
    REPORTS_DIR,
    SEED,
    Periods,
)
from src.model.data import filter_period
from src.model.lgbm import import_lightgbm, prepare_X

JST = timezone(timedelta(hours=9))
VARIANTS = ("pre", "preview")
TECH_NAMES = {1: "nige", 2: "sashi", 3: "makuri", 4: "makurisashi", 5: "nuki", 6: "megumare"}
TECH_JA = {"nige": "逃げ", "sashi": "差し", "makuri": "まくり", "makurisashi": "まくり差し"}
MIN_CLASS_SHARE = 0.008  # これ未満の (コース, 決まり手) は other にまとめる
OTHER = "other"
FORBIDDEN_WORDS = ("最適", "必ず", "おすすめ", "確実")
MIXED_BELOW = 0.25  # 一番の展開の確率がこれ未満なら「混戦」と書く
DEFAULT_TRAIN_FROM = "2016-10-30"


# ---------------------------------------------------------------------------
# 目的変数
# ---------------------------------------------------------------------------


def build_targets(tenkai_df: pd.DataFrame) -> pd.DataFrame:
    """race_id → 1着の予定コース(win_course)、決まり手(tech)、予定コースごとの実際の ST(st_c1〜st_c6)。

    予定コースの艇の枠番は c{k}_entry_number(展開の表)、実際の着順・ST は interim の entries から読む。
    """
    from src.features.build_interim import read_interim

    d0, d1 = pd.to_datetime(tenkai_df["date"]).min().date(), pd.to_datetime(tenkai_df["date"]).max().date()
    ent = read_interim("entries", d0, d1)
    ent = ent[["race_id", "entry_number", "finish_position", "rs_start_timing"]].copy()
    ent["race_id"] = ent["race_id"].astype(str)
    ent["entry_number"] = ent["entry_number"].astype(int)
    fin = ent.set_index(["race_id", "entry_number"])
    t = tenkai_df[["race_id", "technique_number"] + [f"c{k}_entry_number" for k in range(1, 7)]].copy()
    t["race_id"] = t["race_id"].astype(str)
    out = pd.DataFrame({"race_id": t["race_id"]})
    win_course = np.full(len(t), np.nan)
    for k in range(1, 7):
        e = pd.to_numeric(t[f"c{k}_entry_number"], errors="coerce")
        idx = pd.MultiIndex.from_arrays([t["race_id"], e.fillna(-1).astype(int)])
        fp = fin["finish_position"].reindex(idx).to_numpy(dtype=float, na_value=np.nan)
        out[f"st_c{k}"] = fin["rs_start_timing"].reindex(idx).to_numpy(dtype=float, na_value=np.nan)
        win_course = np.where(fp == 1, k, win_course)
    out["win_course"] = win_course
    tech = pd.to_numeric(t["technique_number"], errors="coerce")
    out["tech"] = tech.map(TECH_NAMES)
    return out.reset_index(drop=True)


def who_label(course, tech) -> str | None:
    """(1着の予定コース, 決まり手) → クラス名(例 c4_makuri)。抜き・恵まれは other。"""
    if pd.isna(course) or tech is None or (isinstance(tech, float) and np.isnan(tech)):
        return None
    if tech in ("nuki", "megumare"):
        return OTHER
    return f"c{int(course)}_{tech}"


def choose_classes(labels: pd.Series) -> list[str]:
    """学習期間の比率が MIN_CLASS_SHARE 以上のクラス + other。"""
    share = labels.value_counts(normalize=True)
    keep = sorted([c for c, s in share.items() if s >= MIN_CLASS_SHARE and c != OTHER],
                  key=lambda c: (int(c.split("_")[0][1:]), list(TECH_JA).index(c.split("_", 1)[1])))
    return keep + [OTHER]


def encode(labels: pd.Series, classes: list[str]) -> np.ndarray:
    idx = {c: i for i, c in enumerate(classes)}
    # 欠損(None / NaN)は −1(学習・評価から外す)。pandas は None を NaN にすることがあるので isna で判定する
    return np.array([-1 if pd.isna(c) else idx.get(c, idx[OTHER]) for c in labels], dtype=int)


def top_start_label(st: np.ndarray) -> np.ndarray:
    """予定コースごとの ST (n,6) → トップスタートの予定コース(0〜5)。欠けや1位の同タイムは −1。"""
    ok = ~np.isnan(st).any(axis=1)
    mn = np.nanmin(np.where(np.isnan(st), np.inf, st), axis=1)
    ties = (np.isclose(st, mn[:, None])).sum(axis=1)
    lab = np.where(ok & (ties == 1), np.nanargmin(np.where(np.isnan(st), np.inf, st), axis=1), -1)
    return lab


def faster_label(st: np.ndarray, k: int) -> np.ndarray:
    """予定 k コースの艇が k−1 コースの艇より ST が速いか(1/0)。欠け・同タイムは −1。"""
    a, b = st[:, k - 1], st[:, k - 2]
    lab = np.where(a < b, 1, 0)
    return np.where(np.isnan(a) | np.isnan(b) | np.isclose(a, b), -1, lab)


# ---------------------------------------------------------------------------
# 学習
# ---------------------------------------------------------------------------


def _fit_multi(lgb, X_tr, y_tr, X_va, y_va, n_class: int, cats, params: dict):
    p = {**LGBM_PARAMS, "objective": "multiclass", "num_class": n_class, "metric": "multi_logloss",
         "min_data_in_leaf": 50, **params}
    dtr = lgb.Dataset(X_tr, y_tr, categorical_feature=cats, free_raw_data=False)
    dva = lgb.Dataset(X_va, y_va, categorical_feature=cats, reference=dtr, free_raw_data=False)
    return lgb.train(p, dtr, num_boost_round=5000, valid_sets=[dva], valid_names=["valid"],
                     callbacks=[lgb.early_stopping(EARLY_STOPPING_ROUNDS, verbose=False), lgb.log_evaluation(0)])


def _fit_binary(lgb, X_tr, y_tr, X_va, y_va, cats, params: dict):
    p = {**LGBM_PARAMS, "min_data_in_leaf": 50, **params}
    dtr = lgb.Dataset(X_tr, y_tr, categorical_feature=cats, free_raw_data=False)
    dva = lgb.Dataset(X_va, y_va, categorical_feature=cats, reference=dtr, free_raw_data=False)
    return lgb.train(p, dtr, num_boost_round=5000, valid_sets=[dva], valid_names=["valid"],
                     callbacks=[lgb.early_stopping(EARLY_STOPPING_ROUNDS, verbose=False), lgb.log_evaluation(0)])


def _raw(b, X, it) -> np.ndarray:
    z = b.predict(X, raw_score=True, num_iteration=it)
    return z if z.ndim == 2 else np.column_stack([np.zeros_like(z), z])  # 二値は2クラスのロジットにする


def _proba(Z: np.ndarray, calib: dict) -> np.ndarray:
    return tk.apply_calibration(Z, calib)


def _baselines(y: np.ndarray, n_class: int, train_y: np.ndarray, hist: pd.DataFrame, part: pd.DataFrame) -> dict:
    """B0 全体の比率、B1 場ごとの比率(前日までの累計、α=20 で全体へ縮める)。"""
    c = np.bincount(train_y[train_y >= 0], minlength=n_class).astype(float) + 0.5
    prior = c / c.sum()
    h = hist[hist["y"] >= 0]
    onehot = np.eye(n_class)[h["y"].to_numpy()]
    daily = pd.DataFrame(onehot).assign(date=h["date"].to_numpy(), s=h["stadium_number"].to_numpy())
    cum = daily.groupby(["s", "date"]).sum().sort_index().groupby(level=0).cumsum()
    B1 = np.empty((len(part), n_class))
    for i, (s, d) in enumerate(zip(part["stadium_number"].to_numpy(), part["date"].to_numpy())):
        cnt = np.zeros(n_class)
        if s in cum.index.get_level_values(0):
            cs = cum.loc[s]
            prev = cs[cs.index < d]
            if len(prev):
                cnt = prev.iloc[-1].to_numpy(float)
        B1[i] = (cnt + 20 * prior) / (cnt.sum() + 20)
    return {"B0": tk.multi_logloss(np.tile(prior, (len(y), 1)), y), "B1": tk.multi_logloss(B1, y)}


def train_all(variant: str, periods: Periods | None = None) -> tuple[dict, dict, dict]:
    """A・B のモデルを学習・較正・評価する。戻り値は (boosters, meta, report)。"""
    lgb = import_lightgbm()
    periods = periods or Periods(**{**DEFAULT_PERIODS.to_dict(), "train_from": DEFAULT_TRAIN_FROM})
    feats, cats = tk.feature_lists(variant)
    df = tk.load_tenkai(variant)
    df = df[df["is_valid_result"].astype("boolean").fillna(False).astype(bool)].reset_index(drop=True)
    tg = build_targets(df)
    df = pd.concat([df, tg.drop(columns="race_id")], axis=1)
    df["who"] = [who_label(c, t) for c, t in zip(df["win_course"], df["tech"])]
    st = df[[f"st_c{k}" for k in range(1, 7)]].to_numpy(float)
    df["top"] = top_start_label(st)
    for k in range(2, 7):
        df[f"faster_{k}"] = faster_label(st, k)

    tr = filter_period(df, *periods.range_of("train"))
    va = filter_period(df, *periods.range_of("valid"))
    te = filter_period(df, *periods.range_of("test"))
    classes = choose_classes(tr["who"].dropna())
    for part in (df, tr, va, te):
        part["y_who"] = encode(part["who"], classes)
    hist = df.assign(date=pd.to_datetime(df["date"]))

    X = {n: prepare_X(p, feats, cats) for n, p in (("tr", tr), ("va", va), ("te", te))}
    tasks = {"who": ("y_who", len(classes)), "top": ("top", 6)}
    tasks.update({f"faster_{k}": (f"faster_{k}", 2) for k in range(2, 7)})
    boosters, meta_tasks, report = {}, {}, {"variant": variant, "classes_who": classes, "tasks": {}}
    for name, (col, n_class) in tasks.items():
        mtr, mva, mte = tr[col].to_numpy() >= 0, va[col].to_numpy() >= 0, te[col].to_numpy() >= 0
        if n_class == 2:
            b = _fit_binary(lgb, X["tr"][mtr], tr[col].to_numpy()[mtr], X["va"][mva], va[col].to_numpy()[mva], cats, {})
        else:
            b = _fit_multi(lgb, X["tr"][mtr], tr[col].to_numpy()[mtr], X["va"][mva], va[col].to_numpy()[mva],
                           n_class, cats, {})
        it = int(b.best_iteration) or int(b.current_iteration())
        Zva = _raw(b, X["va"][mva], it)
        calib, cv = tk.select_calibration(Zva, va[col].to_numpy()[mva], va["date"].to_numpy()[mva])
        Pte = _proba(_raw(b, X["te"][mte], it), calib)
        yte = te[col].to_numpy()[mte]
        base = _baselines(yte, n_class, tr[col].to_numpy(), hist.assign(y=hist[col]), te[mte])
        r = {"test_races": int(mte.sum()), "logloss": tk.multi_logloss(Pte, yte), **base,
             "valid_logloss": tk.multi_logloss(_proba(Zva, calib), va[col].to_numpy()[mva]),
             "calibration": calib["method"], "best_iteration": it,
             "class_mean": {str(c): {"pred": float(Pte[:, i].mean()), "actual": float((yte == i).mean())}
                            for i, c in enumerate(classes if name == "who" else (range(1, 7) if name == "top" else (0, 1)))}}
        report["tasks"][name] = r
        boosters[name] = b
        meta_tasks[name] = {"best_iteration": it, "calibration": calib, "n_class": n_class, "cv_logloss": cv}

    # 整合: A の周辺(決まり手ごとの合計)と既存の展開予想、A の「1コースの逃げ」と本体の1コースの艇の1着確率
    Pwho = _proba(_raw(boosters["who"], X["te"], meta_tasks["who"]["best_iteration"]), meta_tasks["who"]["calibration"])
    report["consistency"] = consistency(te, Pwho, classes, variant)

    created = datetime.now(JST)
    mid = f"tenkai2_{variant}_{created:%Y%m%d}_" + hashlib.sha256(json.dumps(
        {k: v["best_iteration"] for k, v in meta_tasks.items()}, sort_keys=True).encode()).hexdigest()[:4]
    meta = {"model_id": mid, "kind": "tenkai2", "variant": variant, "created_at": created.isoformat(timespec="seconds"),
            "train_until": periods.train_to, "periods": periods.to_dict(), "features": feats, "categorical": cats,
            "classes_who": classes, "tasks": meta_tasks, "seed": SEED}
    report["model_id"] = mid
    return boosters, meta, report


def consistency(te: pd.DataFrame, Pwho: np.ndarray, classes: list[str], variant: str) -> dict:
    """A の周辺の合計が、既存の展開予想(決まり手)・本体の1着確率と大きくずれないか。"""
    from src.model.data import features_path, load_features
    from src.model.lgbm import load_model
    from src.model.report import predict_p

    out = {}
    # 決まり手ごとの合計 vs 既存の展開予想(test 期間で同じレース)
    try:
        b, meta = tk.load("latest", variant)
        P0 = tk.predict_proba(b, te, meta)
        for i, t in enumerate(TECH_JA):
            idx = [j for j, c in enumerate(classes) if c.endswith("_" + t)]
            a = Pwho[:, idx].sum(axis=1)
            out[f"tech_{t}"] = {"tenkai2_sum_mean": float(a.mean()), "tenkai_mean": float(P0[:, i].mean()),
                                "mean_abs_diff": float(np.abs(a - P0[:, i]).mean())}
        out["tenkai_model"] = meta["model_id"]
    except FileNotFoundError:
        pass
    # 1コースの艇の1着(A では c1_* の合計)vs 本体の p_win
    try:
        bm, mm = load_model("latest", variant=variant)
        f = load_features(features_path(mm["variant"]))
        f = f[f["race_id"].astype(str).isin(set(te["race_id"].astype(str)))]
        pm = f[["race_id", "entry_number"]].assign(p=predict_p(f, bm, mm), race_id=f["race_id"].astype(str))
        c1 = te[["race_id", "c1_entry_number"]].assign(race_id=te["race_id"].astype(str))
        c1["p_c1_win_a"] = Pwho[:, [j for j, c in enumerate(classes) if c.startswith("c1_")]].sum(axis=1)
        m = c1.merge(pm, left_on=["race_id", "c1_entry_number"], right_on=["race_id", "entry_number"])
        d = m["p"] - m["p_c1_win_a"]
        out["course1_win"] = {"main_model": mm["model_id"], "races": int(len(m)), "main_mean": float(m["p"].mean()),
                              "tenkai2_mean": float(m["p_c1_win_a"].mean()), "mean_abs_diff": float(d.abs().mean()),
                              "corr": float(np.corrcoef(m["p"], m["p_c1_win_a"])[0, 1])}
    except FileNotFoundError:
        pass
    return out


def model_root(variant: str, models_dir=MODELS_DIR) -> Path:
    return Path(models_dir) / "tenkai2" / variant


def save(boosters: dict, meta: dict, models_dir=MODELS_DIR, set_latest: bool = False) -> Path:
    d = model_root(meta["variant"], models_dir) / meta["model_id"]
    d.mkdir(parents=True, exist_ok=True)
    for name, b in boosters.items():
        b.save_model(str(d / f"{name}.txt"), num_iteration=meta["tasks"][name]["best_iteration"])
    (d / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    if set_latest:
        (model_root(meta["variant"], models_dir) / "latest").write_text(meta["model_id"] + "\n", encoding="utf-8")
    return d


def load(model_id: str, variant: str, models_dir=MODELS_DIR) -> tuple[dict, dict]:
    lgb = import_lightgbm()
    root = model_root(variant, models_dir)
    if model_id == "latest":
        model_id = (root / "latest").read_text(encoding="utf-8").strip()
    meta = json.loads((root / model_id / "meta.json").read_text(encoding="utf-8"))
    return {n: lgb.Booster(model_file=str(root / model_id / f"{n}.txt")) for n in meta["tasks"]}, meta


# ---------------------------------------------------------------------------
# 予想と C 展開の一言
# ---------------------------------------------------------------------------


def predict_detail(feats: pd.DataFrame, boosters: dict, meta: dict) -> pd.DataFrame:
    """1行=1レース: p_who_{クラス}(合計1)、p_top_c1〜c6(合計1)、p_faster_c2〜c6、course{k}_entry、headline。"""
    df = feats.reset_index(drop=True)
    X = prepare_X(df, meta["features"], meta["categorical"])
    out = pd.DataFrame({"race_id": df["race_id"].astype(str)})
    for k in range(1, 7):
        e = pd.to_numeric(df.get(f"c{k}_entry_number"), errors="coerce") if f"c{k}_entry_number" in df else None
        out[f"course{k}_entry"] = pd.array(e.fillna(k).astype(int) if e is not None else k, dtype="Int8")
    t = meta["tasks"]
    Pw = _proba(_raw(boosters["who"], X, t["who"]["best_iteration"]), t["who"]["calibration"])
    for i, c in enumerate(meta["classes_who"]):
        out[f"p_who_{c}"] = Pw[:, i]
    Pt = _proba(_raw(boosters["top"], X, t["top"]["best_iteration"]), t["top"]["calibration"])
    for k in range(1, 7):
        out[f"p_top_c{k}"] = Pt[:, k - 1]
    for k in range(2, 7):
        n = f"faster_{k}"
        out[f"p_faster_c{k}"] = _proba(_raw(boosters[n], X, t[n]["best_iteration"]), t[n]["calibration"])[:, 1]
    out["headline"] = [headline(r, meta["classes_who"]) for r in out.to_dict("records")]
    out["model_id"] = meta["model_id"]
    out["variant"] = meta["variant"]
    return out


def headline(row: dict, classes: list[str]) -> str:
    """展開の一言(決まった型)。例: 本線: 1号艇の逃げ(55%)。崩れるなら4号艇のまくり(9%)。カドの4号艇がトップスタートを取る確率 24%。"""
    def desc(c: str) -> str:
        k, t = c.split("_", 1)
        return f"{row[f'course{int(k[1:])}_entry']}号艇の{TECH_JA[t]}"

    probs = sorted(((row[f"p_who_{c}"], c) for c in classes if c != OTHER), reverse=True)
    parts = []
    if probs:
        p1, c1 = probs[0]
        if p1 >= MIXED_BELOW:
            parts.append(f"本線: {desc(c1)}({p1 * 100:.0f}%)。")
            if len(probs) > 1:
                p2, c2 = probs[1]
                parts.append(f"崩れるなら{desc(c2)}({p2 * 100:.0f}%)。")
        else:
            # 一番の展開でも確率が低いときは「本線」と言わない
            parts.append(f"混戦。最も確率が高いのは{desc(c1)}({p1 * 100:.0f}%)")
            if len(probs) > 1:
                p2, c2 = probs[1]
                parts.append(f"、次いで{desc(c2)}({p2 * 100:.0f}%)")
            parts.append("。")
    tops = [(row[f"p_top_c{k}"], k) for k in range(1, 7)]
    pk = row["p_top_c4"]
    if pk >= 0.2:
        parts.append(f"カドの{row['course4_entry']}号艇がトップスタートを取る確率 {pk * 100:.0f}%。")
    else:
        p, k = max(tops)
        parts.append(f"トップスタートの候補は{k}コースの{row[f'course{k}_entry']}号艇({p * 100:.0f}%)。")
    text = "".join(parts)
    for w in FORBIDDEN_WORDS:
        if w in text:
            raise ValueError(f"使ってはいけない言葉: {w}")
    return text


def output_path(out_dir, ymd: str, variant: str) -> Path:
    return Path(out_dir) / f"{ymd}_{variant}_tenkai_detail.parquet"


def predict_main(args) -> None:
    from src.model.predict_tenkai import _write, attach_pv_source, pv_complete

    d = pd.Timestamp(args.date)
    ymd = d.strftime("%Y%m%d")
    now = pd.Timestamp(args.now) if args.now else pd.Timestamp(datetime.now(JST))
    if now.tzinfo is None:
        now = now.tz_localize(JST)
    path = output_path(args.out_dir, ymd, args.variant)
    if path.exists() and not args.force and not args.per_race_lock:
        print(f"{path.name} は確定済みのためスキップしました(上書きは --force)")
        return
    feats = pd.read_parquet(Path(args.features) if args.features else FEATURES_DIR / "today" / f"{ymd}_tenkai_{args.variant}.parquet")
    boosters, meta = load(args.model_id, args.variant, args.models_dir)

    def make(part):
        o = predict_detail(part, boosters, meta).assign(predicted_at=now, locked_at=now)
        return attach_pv_source(o, part, d) if args.variant == "preview" else o

    if args.per_race_lock and not args.force:
        from src.features.build_interim import read_interim

        races = read_interim("races", d.date(), d.date())
        ca = races.set_index(races["race_id"].astype(str))["closed_at"]
        existing = pd.read_parquet(path) if path.exists() else None
        locked = set(existing["race_id"]) if existing is not None else set()
        rid = feats["race_id"].astype(str)
        comp = pv_complete(feats).to_numpy()
        c = pd.to_datetime(ca.reindex(rid.to_numpy())).to_numpy()
        status = np.array(["locked" if r in locked else "closed" if pd.notna(x) and now >= pd.Timestamp(x)
                           else "added" if ok and pd.notna(x) else "waiting" for r, ok, x in zip(rid, comp, c)])
        counts = {k: int((status == k).sum()) for k in ("added", "locked", "waiting", "closed")}
        if counts["added"]:
            new = make(feats[status == "added"])
            out = new if existing is None else pd.concat([existing, new], ignore_index=True)
            _write(out.sort_values("race_id").reset_index(drop=True), path)
        print(f"[tenkai2 per-race-lock {now.isoformat(timespec='minutes')}] 追加 {counts['added']} / 確定済み {counts['locked']} / "
              f"直前情報待ち {counts['waiting']} / 締切後で見送り {counts['closed']} → {path}")
        return
    out = make(feats)
    if path.exists():
        sup = path.parent / "superseded"
        sup.mkdir(exist_ok=True)
        old = str(pd.read_parquet(path, columns=["model_id"])["model_id"].iloc[0])
        path.replace(sup / f"{ymd}_{args.variant}_tenkai_detail_{old}.parquet")
    _write(out, path)
    print(f"{len(out)} レース → {path}\n例: {out['headline'].iloc[0]}")


def render(report: dict) -> str:
    L = [f"# 踏み込んだ展開予想 {report['model_id']}({report['variant']})", "",
         "`python -m src.model.tenkai2` が自動で生成。学習は8月まで、較正は9月、10月で評価。", "",
         f"A のクラス: {', '.join(report['classes_who'])}", "",
         "| 予想 | test レース | logloss | 基準 B0(全体) | 基準 B1(場ごと) | 較正 |", "|---|---|---|---|---|---|"]
    names = {"who": "A 誰の決まり手か", "top": "B トップスタートの予定コース"}
    names.update({f"faster_{k}": f"B {k}コースが{k - 1}コースより先" for k in range(2, 7)})
    for n, r in report["tasks"].items():
        L.append(f"| {names[n]} | {r['test_races']} | {r['logloss']:.4f} | {r['B0']:.4f} | {r['B1']:.4f} | {r['calibration']} |")
    c = report.get("consistency", {})
    if c:
        L += ["", "## 整合(test)", ""]
        for t in TECH_JA:
            if f"tech_{t}" in c:
                v = c[f"tech_{t}"]
                L.append(f"- {TECH_JA[t]}: A の合計 {v['tenkai2_sum_mean']:.3f} / 既存の展開予想 {v['tenkai_mean']:.3f}"
                         f"(1レースあたりの差の平均 {v['mean_abs_diff']:.3f})")
        if "course1_win" in c:
            v = c["course1_win"]
            L.append(f"- 1コースの艇の1着: 本体 {v['main_mean']:.3f} / A の c1_* 合計 {v['tenkai2_mean']:.3f}"
                     f"(差の平均 {v['mean_abs_diff']:.3f}、相関 {v['corr']:.3f})")
    L += ["", "## クラスごとの予測平均と実際(test、A)", ""]
    for cl, v in report["tasks"]["who"]["class_mean"].items():
        L.append(f"- {cl}: {v['pred']:.3f} / {v['actual']:.3f}")
    L.append("")
    return "\n".join(L)


def main() -> None:
    parser = argparse.ArgumentParser(description="踏み込んだ展開予想")
    parser.add_argument("--variant", default="pre", choices=list(VARIANTS))
    parser.add_argument("--set-latest", action="store_true")
    parser.add_argument("--predict", action="store_true")
    parser.add_argument("--date")
    parser.add_argument("--model-id", default="latest")
    parser.add_argument("--features", default=None)
    parser.add_argument("--models-dir", default=str(MODELS_DIR))
    parser.add_argument("--out-dir", default=str(PREDICTIONS_DIR))
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--per-race-lock", action="store_true")
    parser.add_argument("--now", default=None)
    args = parser.parse_args()
    if args.predict:
        predict_main(args)
        return
    boosters, meta, report = train_all(args.variant)
    d = save(boosters, meta, args.models_dir, args.set_latest)
    (REPORTS_DIR / f"tenkai2_{args.variant}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (REPORTS_DIR / f"tenkai2_{args.variant}.md").write_text(render(report), encoding="utf-8")
    for n, r in report["tasks"].items():
        print(f"{n}: logloss {r['logloss']:.4f}  B0 {r['B0']:.4f}  B1 {r['B1']:.4f}")
    print(f"→ {d}")


if __name__ == "__main__":
    main()
