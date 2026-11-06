"""当日の予想(spec/03 7章)。

CLI:
    python -m src.model.predict --date 2026-11-02 --variant pre --model-id latest
入力: data/features/today/YYYYMMDD_{variant}.parquet(特徴量担当の当日用。目的変数は null)
出力: data/predictions/YYYYMMDD_{variant}.parquet(1行=1艇。export の入力)
    列: race_id, entry_number, model_id, variant, predicted_at, locked_at, score, p_win, p_top2, p_top3
      直前版(preview 系)だけ pv_source(直前情報の出どころ official / openapi / none。艇ごと。無い行は不明)も付ける
      data/predictions/YYYYMMDD_{variant}_contrib.parquet(根拠トップ3。spec/08 5.2)
    列: race_id, entry_number, rank, feature, contribution, label, value, value_public, model_id

予想の確定(spec/08 5.1): 同じ日・同じ variant の予想ファイルが既にあれば、何も書かずに終了コード0で
終わる。--force のときだけ上書きし、旧版を data/predictions/superseded/YYYYMMDD_{variant}_{旧model_id}.parquet
(根拠は ..._contrib.parquet)へ退避する。locked_at は最初に書いた時刻(= predicted_at)。

--variant preview --per-race-lock(日中ジョブ用): 既にファイルにあるレースは行も locked_at もそのまま。
未確定のうち、全艇の直前情報がそろい、締切前のレースだけを予想して追記する(locked_at はレースごとの追記時刻)。
直前情報が無いレースは追記しない(pre へのフォールバックで確定させない)。締切後のレースは追加しない。
根拠ファイルも同じ規則で追記する。現在時刻は --now で渡せる。--force を付けたときは従来どおりの全体上書き。

予想は6艇で行う(欠場は予想時点では未知。欠場が判明したときの再正規化は export 時に
combinations.py の関数で行う)。組合せ確率は保存しない(export 時に p_win から計算し直す)。
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from src.model import combinations as cb
from src.model.config import FEATURES_DIR, MODELS_DIR, PREDICTIONS_DIR, VARIANTS, is_preview_variant, pre_variant_of
from src.model.calibration import apply_rows, calibration_from_meta
from src.model.data import race_matrix
from src.model.lgbm import gammas_from_meta, load_model, prepare_X, raw_scores

JST = timezone(timedelta(hours=9))
OUTPUT_COLUMNS = ["race_id", "entry_number", "model_id", "variant", "predicted_at", "locked_at", "score",
                  "p_win", "p_top2", "p_top3"]
CONTRIB_COLUMNS = ["race_id", "entry_number", "rank", "feature", "contribution", "label", "value", "value_public",
                   "model_id"]
TOP_N_REASONS = 3


def predict_frame(features: pd.DataFrame, booster, meta: dict, predicted_at: datetime | None = None) -> pd.DataFrame:
    """特徴量(1行=1艇)から予想の表を作る。"""
    predicted_at = predicted_at or datetime.now(JST)
    df = features.reset_index(drop=True)
    score = raw_scores(booster, df, meta)
    p_win = apply_rows(df["race_id"].to_numpy(), df["entry_number"].to_numpy(), score, calibration_from_meta(meta))

    tmp = df[["race_id", "entry_number"]].assign(p_win=p_win)
    race_ids, P = race_matrix(tmp, "p_win", fill=0.0)
    P = P / P.sum(axis=1, keepdims=True)
    fp = cb.finish_prob_matrix(P, *gammas_from_meta(meta))
    r_idx = pd.Index(race_ids).get_indexer(df["race_id"])
    e_idx = df["entry_number"].to_numpy().astype(int) - 1

    out = pd.DataFrame(
        {
            "race_id": df["race_id"].astype(str).to_numpy(),
            "entry_number": df["entry_number"].astype("int8").to_numpy(),
            "model_id": meta["model_id"],
            "variant": meta["variant"],
            "predicted_at": pd.Timestamp(predicted_at),
            "locked_at": pd.Timestamp(predicted_at),
            "score": score,
            "p_win": fp["p_win"][r_idx, e_idx],
            "p_top2": fp["p_top2"][r_idx, e_idx],
            "p_top3": fp["p_top3"][r_idx, e_idx],
        }
    )
    sums = out.groupby("race_id")["p_win"].sum()
    if not np.allclose(sums, 1.0, atol=1e-6):
        raise AssertionError("p_win の合計が1にならないレースがあります")
    return out.sort_values(["race_id", "entry_number"]).reset_index(drop=True)[OUTPUT_COLUMNS]


def contrib_frame(features: pd.DataFrame, booster, meta: dict, top_n: int = TOP_N_REASONS) -> pd.DataFrame:
    """各艇の寄与(LightGBM の pred_contrib。生スコア=logit 上の値)の絶対値上位 top_n を返す。

    寄与が0の特徴量は出さない(そのため top_n 件より少ないことがある)。
    """
    from src.model.feature_labels import is_value_public, label_for

    df = features.reset_index(drop=True)
    feats = meta["features"]
    X = prepare_X(df, feats, meta["categorical"])
    C = booster.predict(X, pred_contrib=True, num_iteration=meta.get("best_iteration"))[:, : len(feats)]
    order = np.argsort(-np.abs(C), axis=1, kind="stable")[:, :top_n]
    rows = []
    for i in range(len(df)):
        for rank, j in enumerate(order[i], start=1):
            c = float(C[i, j])
            if c == 0.0:
                continue
            name = feats[j]
            v = df.at[i, name]
            rows.append((str(df.at[i, "race_id"]), int(df.at[i, "entry_number"]), rank, name, c, label_for(name),
                         None if pd.isna(v) else float(v), is_value_public(name), meta["model_id"]))
    out = pd.DataFrame(rows, columns=CONTRIB_COLUMNS)
    out["entry_number"] = out["entry_number"].astype("int8")
    out["rank"] = out["rank"].astype("int8")
    return out


def fallback_to_pre(out: pd.DataFrame, feats: pd.DataFrame, ymd: str, args, contrib: pd.DataFrame | None = None):
    """直前情報が欠けたレース(pv_exhibition_time に欠損)は pre の latest モデルの予想に置き換える。

    置き換えた行は model_id / variant が pre のものになる(export はレースごとに variant を見られる)。
    """
    missing = set(feats.loc[feats["pv_exhibition_time"].isna(), "race_id"].astype(str))
    if not missing:
        return out, contrib
    pre_variant = pre_variant_of(args.variant)
    pre_path = FEATURES_DIR / "today" / f"{ymd}_{pre_variant}.parquet"
    if not pre_path.exists():
        print(f"警告: 直前情報が欠けたレースが {len(missing)} 件ありますが、{pre_path} が無いため置き換えません")
        return out, contrib
    pre_booster, pre_meta = load_model(args.fallback_model_id, args.models_dir, pre_variant)
    pre_feats = pd.read_parquet(pre_path)
    pre_feats = pre_feats[pre_feats["race_id"].astype(str).isin(missing)]
    pre_out = predict_frame(pre_feats, pre_booster, pre_meta, out["predicted_at"].iloc[0])
    replaced = set(pre_out["race_id"])
    kept = out[~out["race_id"].isin(replaced)]
    print(f"直前情報が欠けた {len(missing)} レースを pre({pre_meta['model_id']})で予想しました")
    out = pd.concat([kept, pre_out], ignore_index=True).sort_values(["race_id", "entry_number"]).reset_index(drop=True)
    if contrib is not None:
        contrib = pd.concat([contrib[~contrib["race_id"].isin(replaced)], contrib_frame(pre_feats, pre_booster, pre_meta)],
                            ignore_index=True).sort_values(["race_id", "entry_number", "rank"]).reset_index(drop=True)
    return out, contrib


def attach_pv_source(out: pd.DataFrame, feats: pd.DataFrame, date) -> pd.DataFrame:
    """直前情報の出どころ(official / openapi / none)を艇ごとに pv_source 列として付ける(M-18 の集計用)。

    特徴量に pv_source があればそれを、無ければ interim の entries から読む。分からなければ null(不明)。
    既に pv_source を持つ行(確定済みの行)は変えない。
    """
    if "pv_source" in out.columns and out["pv_source"].notna().all():
        return out
    src = None
    if "pv_source" in feats.columns:
        src = feats[["race_id", "entry_number", "pv_source"]].copy()
    else:
        try:
            from src.features.build_interim import read_interim

            d = pd.Timestamp(date).date()
            ent = read_interim("entries", d, d)
            if "pv_source" in ent.columns:
                src = ent[["race_id", "entry_number", "pv_source"]].copy()
        except Exception as e:  # noqa: BLE001 - 出どころが分からなくても予想は止めない
            print(f"警告: pv_source を読めませんでした({e})。不明として記録します")
    out = out.copy()
    if src is None:
        if "pv_source" not in out.columns:
            out["pv_source"] = pd.Series([None] * len(out), dtype="string")
        return out
    src["race_id"] = src["race_id"].astype(str)
    src["entry_number"] = src["entry_number"].astype("int8")
    src = src.drop_duplicates(["race_id", "entry_number"]).rename(columns={"pv_source": "_pv_source_new"})
    out = out.merge(src, on=["race_id", "entry_number"], how="left")
    if "pv_source" in out.columns:
        out["pv_source"] = out["pv_source"].astype("string").fillna(out["_pv_source_new"].astype("string"))
    else:
        out["pv_source"] = out["_pv_source_new"].astype("string")
    return out.drop(columns="_pv_source_new")


def _write_atomic(df: pd.DataFrame, path: Path) -> None:
    tmp = path.with_suffix(".tmp")
    df.to_parquet(tmp, index=False)
    tmp.replace(path)  # export が書きかけを読まないように置き換える


def output_paths(out_dir: str | Path, ymd: str, variant: str) -> tuple[Path, Path]:
    """(予想ファイル, 根拠ファイル) のパス。"""
    d = Path(out_dir)
    return d / f"{ymd}_{variant}.parquet", d / f"{ymd}_{variant}_contrib.parquet"


def is_locked(out_dir: str | Path, ymd: str, variant: str) -> bool:
    """その日・その variant の確定版があるか。"""
    return output_paths(out_dir, ymd, variant)[0].exists()


def write_locked(out: pd.DataFrame, contrib: pd.DataFrame | None, out_dir: str | Path, ymd: str, variant: str,
                 force: bool = False) -> str:
    """確定版として書く。戻り値は "written" / "skipped"(確定済み)/ "replaced"(--force で上書き)。"""
    pred_path, contrib_path = output_paths(out_dir, ymd, variant)
    pred_path.parent.mkdir(parents=True, exist_ok=True)
    status = "written"
    if pred_path.exists():
        if not force:
            return "skipped"
        old = pd.read_parquet(pred_path, columns=["model_id"])
        old_id = str(old["model_id"].mode().iloc[0]) if len(old) else "unknown"
        sup = pred_path.parent / "superseded"
        sup.mkdir(exist_ok=True)
        pred_path.replace(sup / f"{ymd}_{variant}_{old_id}.parquet")
        if contrib_path.exists():
            contrib_path.replace(sup / f"{ymd}_{variant}_{old_id}_contrib.parquet")
        status = "replaced"
    if contrib is not None:
        _write_atomic(contrib, contrib_path)  # 根拠を先に書き、予想ファイルを最後に置く(確定の印)
    _write_atomic(out, pred_path)
    return status


# ---------------------------------------------------------------------------
# レースごとの確定(GitHub Actions の日中ジョブ用。--variant preview --per-race-lock)
# ---------------------------------------------------------------------------


def race_status(feats: pd.DataFrame, closed_at: pd.Series, now: datetime, locked: set) -> pd.Series:
    """レースごとの状態: locked(確定済み)/ ready(追記する)/ waiting(直前情報待ち)/ closed(締切後で見送り)。

    ready = 未確定・6艇そろって全艇の直前情報(pv_exhibition_time)がある・締切前(now < closed_at)。
    締切時刻が不明なレースは追記しない(waiting に数える)。
    """
    g = feats.assign(_pv=feats["pv_exhibition_time"].notna()).groupby(feats["race_id"].astype(str))
    complete = (g["_pv"].sum() == 6) & (g.size() == 6)
    now_ts = pd.Timestamp(now)
    ca = pd.to_datetime(closed_at.reindex(complete.index))
    before = ca.notna() & (now_ts < ca)
    after = ca.notna() & (now_ts >= ca)
    status = pd.Series("waiting", index=complete.index)
    status[after] = "closed"
    status[complete & before] = "ready"
    status[status.index.isin(locked)] = "locked"
    return status


def per_race_update(
    feats: pd.DataFrame,
    closed_at: pd.Series,
    now: datetime,
    booster,
    meta: dict,
    existing: pd.DataFrame | None,
    existing_contrib: pd.DataFrame | None,
) -> tuple[pd.DataFrame, pd.DataFrame | None, dict]:
    """確定済みの行はそのままにして、ready のレースだけを予想して追記した (予想, 根拠, 件数) を返す。

    追記した行の predicted_at と locked_at は now(レースごとの追記時刻)。
    """
    locked = set(existing["race_id"].astype(str)) if existing is not None else set()
    st = race_status(feats, closed_at, now, locked)
    ready = set(st[st == "ready"].index)
    counts = {k: int((st == k).sum()) for k in ("ready", "locked", "waiting", "closed")}
    counts["added"] = counts.pop("ready")
    if not ready:
        return existing, existing_contrib, counts
    part = feats[feats["race_id"].astype(str).isin(ready)]
    new = attach_pv_source(predict_frame(part, booster, meta, now), part, pd.Timestamp(now).tz_convert(JST).date()
                           if pd.Timestamp(now).tzinfo else pd.Timestamp(now).date())
    new_c = contrib_frame(part, booster, meta)
    out = new if existing is None else pd.concat([existing, new], ignore_index=True)
    con = new_c if existing_contrib is None else pd.concat([existing_contrib, new_c], ignore_index=True)
    out = out.sort_values(["race_id", "entry_number"]).reset_index(drop=True)
    con = con.sort_values(["race_id", "entry_number", "rank"]).reset_index(drop=True)
    return out, con, counts


def load_closed_at(feats: pd.DataFrame, date: pd.Timestamp) -> pd.Series:
    """レースの締切時刻(race_id → JST の Timestamp)。特徴量に closed_at が無ければ interim の races から読む。"""
    if "closed_at" in feats.columns:
        return feats.groupby(feats["race_id"].astype(str))["closed_at"].first()
    from src.features.build_interim import read_interim

    races = read_interim("races", date.date(), date.date())
    return races.set_index(races["race_id"].astype(str))["closed_at"]


def main_per_race(args, ymd: str) -> None:
    now = pd.Timestamp(args.now) if args.now else pd.Timestamp(datetime.now(JST))
    if now.tzinfo is None:
        now = now.tz_localize(JST)
    path = Path(args.features) if args.features else FEATURES_DIR / "today" / f"{ymd}_{args.variant}.parquet"
    feats = pd.read_parquet(path)
    pred_path, contrib_path = output_paths(args.out_dir, ymd, args.variant)
    existing = pd.read_parquet(pred_path) if pred_path.exists() else None
    existing_c = pd.read_parquet(contrib_path) if contrib_path.exists() else None
    closed_at = load_closed_at(feats, pd.Timestamp(args.date))
    booster, meta = load_model(args.model_id, args.models_dir, args.variant)
    out, con, c = per_race_update(feats, closed_at, now, booster, meta, existing, existing_c)
    if c["added"]:
        pred_path.parent.mkdir(parents=True, exist_ok=True)
        _write_atomic(con, contrib_path)  # 根拠を先に、予想ファイルを最後に置く
        _write_atomic(out, pred_path)
    print(f"[per-race-lock {now.isoformat(timespec='minutes')}] 追加 {c['added']} / 確定済み {c['locked']} / "
          f"直前情報待ち {c['waiting']} / 締切後で見送り {c['closed']}(model_id={meta['model_id']})→ {pred_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="当日の予想")
    parser.add_argument("--date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--variant", default="pre", choices=list(VARIANTS))
    parser.add_argument("--model-id", default="latest")
    parser.add_argument("--features", default=None, help="省略時は data/features/today/YYYYMMDD_{variant}.parquet")
    parser.add_argument("--models-dir", default=str(MODELS_DIR))
    parser.add_argument("--out-dir", default=str(PREDICTIONS_DIR))
    parser.add_argument("--fallback-model-id", default="latest",
                        help="直前情報が欠けたレースに使う pre 系モデル(既定は対応する pre 系 variant の latest)")
    parser.add_argument("--force", action="store_true", help="確定版を上書きする(旧版は superseded/ へ退避)")
    parser.add_argument("--per-race-lock", action="store_true",
                        help="直前版をレースごとに確定して追記する(直前情報がそろった締切前のレースだけ)")
    parser.add_argument("--now", default=None, help="現在時刻(--per-race-lock 用。例 2026-11-05T10:30+09:00)")
    args = parser.parse_args()

    ymd = pd.Timestamp(args.date).strftime("%Y%m%d")
    if args.per_race_lock and not args.force:
        if not is_preview_variant(args.variant):
            parser.error("--per-race-lock は preview 系の variant だけで使えます")
        main_per_race(args, ymd)
        return
    if is_locked(args.out_dir, ymd, args.variant) and not args.force:
        print(f"{ymd}_{args.variant} は確定済みのためスキップしました(上書きは --force)")
        return
    path = Path(args.features) if args.features else FEATURES_DIR / "today" / f"{ymd}_{args.variant}.parquet"
    booster, meta = load_model(args.model_id, args.models_dir, args.variant)
    if meta["variant"] != args.variant:
        raise ValueError(f"モデルの variant({meta['variant']})と --variant({args.variant})が違います")
    feats = pd.read_parquet(path)
    out = predict_frame(feats, booster, meta)
    contrib = contrib_frame(feats, booster, meta)
    if is_preview_variant(args.variant):
        out, contrib = fallback_to_pre(out, feats, ymd, args, contrib)
        out = attach_pv_source(out, feats, pd.Timestamp(args.date))

    status = write_locked(out, contrib, args.out_dir, ymd, args.variant, args.force)
    out_path = output_paths(args.out_dir, ymd, args.variant)[0]
    n_races = out["race_id"].nunique()
    max_err = float((out.groupby("race_id")["p_win"].sum() - 1).abs().max())
    print(f"[{status}] {n_races} レース / {len(out)} 艇、model_id={meta['model_id']}、"
          f"p_win 合計の最大誤差={max_err:.2e} → {out_path}")


if __name__ == "__main__":
    main()
