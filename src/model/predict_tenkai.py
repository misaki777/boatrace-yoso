"""展開予想(決まり手の確率)の当日予想。spec/10 3.1。

CLI:
    python -m src.model.predict_tenkai --date 2026-11-06 --variant pre
    python -m src.model.predict_tenkai --date 2026-11-06 --variant preview --per-race-lock [--now ...]
入力: data/features/today/YYYYMMDD_tenkai_{variant}.parquet(F-9)
出力: data/predictions/YYYYMMDD_{variant}_tenkai.parquet(spec/10 3.1 のファイル名)
    列: race_id, p_nige, p_sashi, p_makuri, p_makurisashi, p_nuki, p_megumare(合計1)
        + course1_entry(予定1コースの艇の枠番。前日版は1、直前版は展示の進入), model_id, variant, predicted_at, locked_at
        + 直前版だけ pv_source(直前情報の出どころ official / openapi / none。無い行は不明)

確定の規則は本体の predict と同じ(spec/08 5.1):
- 既定: ファイルがあれば何もしない(終了コード0)。--force で上書きし、旧版は superseded/ へ退避。
- --per-race-lock(preview): ファイルにあるレースはそのまま。未確定のうち、6艇すべての展示タイムがそろい、
  締切前のレースだけを追記する(locked_at はレースごとの追記時刻)。直前情報が無いレースは追記しない。
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from src.model.config import FEATURES_DIR, MODELS_DIR, PREDICTIONS_DIR
from src.model.tenkai import CLASSES, PROB_COLUMNS, TENKAI_VARIANTS, load, predict_proba

JST = timezone(timedelta(hours=9))
OUTPUT_COLUMNS = ["race_id", *PROB_COLUMNS, "course1_entry", "model_id", "variant", "predicted_at", "locked_at"]


def output_path(out_dir: str | Path, ymd: str, variant: str) -> Path:
    return Path(out_dir) / f"{ymd}_{variant}_tenkai.parquet"


def predict_frame(feats: pd.DataFrame, booster, meta: dict, now: datetime) -> pd.DataFrame:
    """1行=1レースの確率表。"""
    df = feats.reset_index(drop=True)
    P = predict_proba(booster, df, meta)
    P = P / P.sum(axis=1, keepdims=True)
    out = pd.DataFrame(P, columns=PROB_COLUMNS)
    out.insert(0, "race_id", df["race_id"].astype(str).to_numpy())
    c1 = pd.to_numeric(df.get("c1_entry_number"), errors="coerce") if "c1_entry_number" in df else None
    out["course1_entry"] = pd.array(np.where(np.isnan(c1), 1, c1) if c1 is not None else 1, dtype="Int8")
    out["model_id"] = meta["model_id"]
    out["variant"] = meta["variant"]
    out["predicted_at"] = pd.Timestamp(now)
    out["locked_at"] = pd.Timestamp(now)
    assert np.allclose(out[PROB_COLUMNS].sum(axis=1), 1, atol=1e-9)
    return out[OUTPUT_COLUMNS].sort_values("race_id").reset_index(drop=True)


def attach_pv_source(out: pd.DataFrame, feats: pd.DataFrame, date) -> pd.DataFrame:
    """直前情報の出どころ(official / openapi / none)をレースごとに pv_source 列として付ける(M-18 用)。

    特徴量に pv_source があればそれを、無ければ interim の races から読む。分からなければ null(不明)。
    """
    out = out.copy()
    src = None
    if "pv_source" in feats.columns:
        src = feats[["race_id", "pv_source"]]
    else:
        try:
            from src.features.build_interim import read_interim

            d = pd.Timestamp(date).date()
            races = read_interim("races", d, d)
            if "pv_source" in races.columns:
                src = races[["race_id", "pv_source"]]
        except Exception as e:  # noqa: BLE001
            print(f"警告: pv_source を読めませんでした({e})。不明として記録します")
    if src is None:
        out["pv_source"] = pd.Series([None] * len(out), dtype="string")
        return out
    m = src.assign(race_id=src["race_id"].astype(str)).drop_duplicates("race_id").set_index("race_id")["pv_source"]
    out["pv_source"] = out["race_id"].map(m).astype("string")
    return out


def pv_complete(feats: pd.DataFrame) -> pd.Series:
    """6艇すべての展示タイムがあるレースか(race_id → bool)。"""
    cols = [f"c{k}_pv_exhibition_time" for k in range(1, 7)]
    ok = feats[cols].notna().all(axis=1) if all(c in feats.columns for c in cols) else pd.Series(False, feats.index)
    return pd.Series(ok.to_numpy(), index=feats["race_id"].astype(str).to_numpy())


def per_race_update(feats, closed_at: pd.Series, now, booster, meta, existing: pd.DataFrame | None):
    """確定済みの行はそのままにして、追記できるレースだけを足す。戻り値は (表, 件数)。"""
    locked = set(existing["race_id"]) if existing is not None else set()
    rid = feats["race_id"].astype(str)
    complete = pv_complete(feats)
    ca = pd.to_datetime(closed_at.reindex(rid.to_numpy())).to_numpy()
    now_ts = pd.Timestamp(now)
    status = []
    for r, comp, c in zip(rid, complete.to_numpy(), ca):
        if r in locked:
            status.append("locked")
        elif pd.notna(c) and now_ts >= pd.Timestamp(c):
            status.append("closed")
        elif comp and pd.notna(c):
            status.append("added")
        else:
            status.append("waiting")
    status = np.array(status)
    counts = {k: int((status == k).sum()) for k in ("added", "locked", "waiting", "closed")}
    if not counts["added"]:
        return existing, counts
    new = predict_frame(feats[status == "added"], booster, meta, now)
    if meta.get("variant", "").startswith("preview"):
        new = attach_pv_source(new, feats, pd.Timestamp(now).date())
    out = new if existing is None else pd.concat([existing, new], ignore_index=True)
    return out.sort_values("race_id").reset_index(drop=True), counts


def _write(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    df.to_parquet(tmp, index=False)
    tmp.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser(description="展開予想(決まり手の確率)の当日予想")
    parser.add_argument("--date", required=True)
    parser.add_argument("--variant", default="pre", choices=list(TENKAI_VARIANTS))
    parser.add_argument("--model-id", default="latest")
    parser.add_argument("--features", default=None)
    parser.add_argument("--models-dir", default=str(MODELS_DIR))
    parser.add_argument("--out-dir", default=str(PREDICTIONS_DIR))
    parser.add_argument("--force", action="store_true", help="確定版を上書きする(旧版は superseded/ へ退避)")
    parser.add_argument("--per-race-lock", action="store_true", help="preview: レースごとに確定して追記する")
    parser.add_argument("--now", default=None)
    args = parser.parse_args()

    d = pd.Timestamp(args.date)
    ymd = d.strftime("%Y%m%d")
    now = pd.Timestamp(args.now) if args.now else pd.Timestamp(datetime.now(JST))
    if now.tzinfo is None:
        now = now.tz_localize(JST)
    path = output_path(args.out_dir, ymd, args.variant)
    if path.exists() and not args.force and not args.per_race_lock:
        print(f"{path.name} は確定済みのためスキップしました(上書きは --force)")
        return

    fpath = Path(args.features) if args.features else FEATURES_DIR / "today" / f"{ymd}_tenkai_{args.variant}.parquet"
    feats = pd.read_parquet(fpath)
    booster, meta = load(args.model_id, args.variant, args.models_dir)

    if args.per_race_lock and not args.force:
        if args.variant != "preview":
            parser.error("--per-race-lock は preview だけで使えます")
        from src.features.build_interim import read_interim

        races = read_interim("races", d.date(), d.date())
        closed_at = races.set_index(races["race_id"].astype(str))["closed_at"]
        existing = pd.read_parquet(path) if path.exists() else None
        out, c = per_race_update(feats, closed_at, now, booster, meta, existing)
        if c["added"]:
            _write(out, path)
        print(f"[tenkai per-race-lock {now.isoformat(timespec='minutes')}] 追加 {c['added']} / 確定済み {c['locked']} / "
              f"直前情報待ち {c['waiting']} / 締切後で見送り {c['closed']}(model_id={meta['model_id']})→ {path}")
        return

    out = predict_frame(feats, booster, meta, now)
    if args.variant.startswith("preview"):
        out = attach_pv_source(out, feats, d)
    if path.exists():  # --force
        old_id = str(pd.read_parquet(path, columns=["model_id"])["model_id"].mode().iloc[0])
        sup = path.parent / "superseded"
        sup.mkdir(exist_ok=True)
        path.replace(sup / f"{ymd}_{args.variant}_tenkai_{old_id}.parquet")
    _write(out, path)
    print(f"{len(out)} レース、model_id={meta['model_id']}、逃げの平均 {out['p_nige'].mean():.3f} → {path}")


if __name__ == "__main__":
    main()
