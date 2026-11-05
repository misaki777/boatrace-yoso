"""interim → 特徴量テーブル(1行=1艇)。

使い方:
    # 学習用(期間)。data/features/features_{variant}.parquet
    python -m src.features.build_features --variant pre --from 2026-01-01 --to 2026-11-01
    # 当日用(目的変数なし)。data/features/today/YYYYMMDD_{variant}.parquet
    python -m src.features.build_features --variant pre --date 2026-11-02

学習用と当日用は同じ関数 make_feature_frame() を通る(02_features.md 4章)。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from src.common import codes
from src.features import feature_sets as fs
from src.features import parse
from src.features.build_interim import INTERIM_DIR, read_interim
from src.features.history import assign_series, compute_h_features
from src.features.style import compute_style_h, compute_style_r
from src.features.raw_source import LoadRaw, load_day, make_loader

ROOT = Path(__file__).resolve().parents[2]
FEATURES_DIR = ROOT / "data" / "features"
JST = timezone(timedelta(hours=9))

# ---------------------------------------------------------------------------
# P: 出走表
# ---------------------------------------------------------------------------


def classify_race_kind(subtitle: str | None) -> str:
    """subtitle をキーワードで分類する(2.2節 #6)。判定の順番に意味がある。"""
    s = subtitle if isinstance(subtitle, str) else ""
    if "準優" in s:
        return "jun_yusho"
    if "優勝" in s:
        return "yusho"
    if any(k in s for k in ("特選", "特賞", "選抜", "ドリーム")):
        return "tokusen"
    if "予選" in s:
        return "yosen"
    if "一般" in s:
        return "ippan"
    return "other"


def compute_p_features(df: pd.DataFrame) -> pd.DataFrame:
    """entries に races の列(grade_number, day_number, subtitle)を結合した表から P 特徴量を作る。"""
    out = pd.DataFrame(index=df.index)
    for c in fs.P_FEATURES:
        if c in df.columns and c not in ("race_kind",):
            out[c] = df[c]
    out["race_kind"] = df["subtitle"].astype("object").map(classify_race_kind).map(fs.RACE_KIND_CODES)
    lwr = df["local_win_rate"].astype("Float64")
    # 当地の出走が少ないと 0 になる(仕様)。null も欠損扱い
    out["local_win_rate_missing"] = (lwr.isna() | (lwr == 0)).astype(float)
    pref = df["stadium_number"].map(codes.STADIUM_PREFECTURE).astype("Float64")
    branch = df["branch_number"].astype("Float64")
    out["is_home_branch"] = (branch == pref).astype("Float64")
    return out


# ---------------------------------------------------------------------------
# R: レース内の相対値
# ---------------------------------------------------------------------------


def _other_max(values: pd.Series, race_id: pd.Series) -> pd.Series:
    """同じレースの「自分以外」の最大値。"""
    v = values.astype(float)
    g = v.groupby(race_id)
    max1 = g.transform("max")
    # 降順で1番目の行(同値なら先の行)だけは、2番目の値と比べる
    order = v.groupby(race_id).rank(ascending=False, method="first")
    second = v.where(order == 2).groupby(race_id).transform("max")
    return max1.where(order != 1, second)


def compute_r_features(df: pd.DataFrame) -> pd.DataFrame:
    """出走表の値から、レース内の相対値を作る。df は entries(1行=1艇)。"""
    rid = df["race_id"]
    wr = df["national_win_rate"].astype("Float64").astype(float)
    st = df["average_start_timing"].astype("Float64").astype(float)
    m2 = df["motor_top_2_percent"].astype("Float64").astype(float)
    out = pd.DataFrame(index=df.index)
    out["r_win_rate_rank"] = wr.groupby(rid).rank(ascending=False, method="min")
    out["r_win_rate_diff_max"] = wr - _other_max(wr, rid)
    out["r_win_rate_diff_mean"] = wr - wr.groupby(rid).transform("mean")
    out["r_st_rank"] = st.groupby(rid).rank(ascending=True, method="min")
    out["r_motor2_rank"] = m2.groupby(rid).rank(ascending=False, method="min")
    # 自分より内側の枠の艇の全国勝率の最大値。1号艇は内側がいないので 0
    order = np.lexsort((df["entry_number"].to_numpy(), rid.to_numpy()))
    s = wr.iloc[order]
    r_sorted = rid.iloc[order]
    inner = s.groupby(r_sorted.values).cummax().groupby(r_sorted.values).shift(1)
    out["r_inner_strength"] = inner.reindex(df.index).fillna(0.0)
    a1 = (df["rank_number"].astype("Float64") == 1).astype(float)
    out["r_a1_count"] = a1.groupby(rid).transform("sum")
    return out


# ---------------------------------------------------------------------------
# V: 直前情報(variant=preview)
# ---------------------------------------------------------------------------


def compute_v_features(df: pd.DataFrame) -> pd.DataFrame:
    """直前情報の特徴量。df は entries に races の pv_ 気象を結合したもの。"""
    rid = df["race_id"]
    out = pd.DataFrame(index=df.index)
    ex = df["pv_exhibition_time"].astype("Float64").astype(float)
    out["pv_exhibition_time"] = ex
    out["r_pv_exhibition_rank"] = ex.groupby(rid).rank(ascending=True, method="min")
    out["r_pv_exhibition_diff_best"] = ex - ex.groupby(rid).transform("min")
    for c in ("pv_start_timing", "pv_course_number", "pv_tilt_adjustment",
              "pv_weight_adjustment", "pv_parts_count", "pv_wind_speed", "pv_wave_height",
              "pv_air_temperature", "pv_water_temperature", "pv_weather_number"):
        out[c] = df[c].astype("Float64").astype(float)
    out["pv_propeller_new"] = df["pv_propeller_new"].astype("Float64").astype(float)
    pc = df["pv_course_number"].astype("Float64")
    out["pv_course_changed"] = (pc != df["entry_number"].astype("Float64")).astype("Float64").astype(float)

    def _wind(v):
        if v is None or pd.isna(v):
            return np.nan
        return fs.WIND_CLASS_CODES[codes.wind_class(int(v))]

    out["pv_wind_class"] = df["pv_wind_direction_number"].astype("object").map(_wind).astype(float)
    return out


# ---------------------------------------------------------------------------
# 組み立て
# ---------------------------------------------------------------------------

RACE_COLS_FOR_ENTRIES = [
    "race_id", "grade_number", "day_number", "subtitle", "title", "is_valid_result",
    "pv_wind_speed", "pv_wind_direction_number", "pv_wave_height", "pv_weather_number",
    "pv_air_temperature", "pv_water_temperature",
]


def make_feature_frame(target_entries: pd.DataFrame, target_races: pd.DataFrame,
                       hist_entries: pd.DataFrame, hist_races: pd.DataFrame,
                       variant: str = "pre", with_target: bool = True,
                       state=None) -> pd.DataFrame:
    """特徴量テーブルを作る(学習用・当日用の共通の入口)。

    state(history_state.HistoryState)を渡すと、hist_* の代わりに履歴の状態から H・style を作る
    (毎日のジョブ用。F-8)。その場合 hist_entries / hist_races は None でよい。

    target_*: 特徴量を作る対象(interim と同じ列)。
    hist_*: H 特徴量の材料になる過去の interim。対象日以降の行が混じっていても、
            H は対象日より前の結果しか使わない。
    with_target=False のとき(当日用)、目的変数と is_absent / is_valid_result は null にする。
    """
    feats = fs.features_for(variant)
    df = target_entries.merge(target_races[RACE_COLS_FOR_ENTRIES], on="race_id", how="left",
                              validate="many_to_one")
    df = df.sort_values(["date", "stadium_number", "race_number", "entry_number"]).reset_index(drop=True)

    # 節は対象と過去をまとめて判定する(当日のレースも前日までと同じ節につなげるため)
    if state is not None:
        hist_races = state.races
    all_races = pd.concat([hist_races[["stadium_number", "date", "title"]],
                           target_races[["stadium_number", "date", "title"]]], ignore_index=True)
    series_map = assign_series(all_races)
    df = df.merge(series_map, on=["stadium_number", "date"], how="left")

    p = compute_p_features(df)
    r = compute_r_features(df)
    h = compute_h_features(df, hist_entries, series_map,
                           starts=state.h_starts if state is not None else None)
    parts = [p, r, h]
    if variant.startswith("preview"):
        parts.append(compute_v_features(df))
    if variant.endswith("_v2"):
        # 予定コース: 前日版は枠番、直前版はスタート展示の進入(無ければ枠番)
        planned = df["entry_number"].astype("Float64")
        if variant.startswith("preview"):
            planned = df["pv_course_number"].astype("Float64").fillna(planned)
        sh = compute_style_h(df, planned, hist_entries, hist_races,
                             starts=state.style_starts if state is not None else None)
        parts += [sh, compute_style_r(df, planned, sh)]
    f = pd.concat(parts, axis=1)

    out = pd.DataFrame({
        "race_id": df["race_id"].astype("string"),
        "date": df["date"],
        "stadium_number": df["stadium_number"].astype("int8"),
        "race_number": df["race_number"].astype("int8"),
        "entry_number": df["entry_number"].astype("int8"),
        "racer_number": df["racer_number"].astype("Int32"),
    })
    if with_target:
        fp = df["finish_position"].astype("Int8")
        has_result = df["place_number"].notna().groupby(df["race_id"]).transform("any")
        out["finish_position"] = fp
        for name, k in (("is_win", 1), ("is_top2", 2), ("is_top3", 3)):
            v = (fp <= k).fillna(False).astype("boolean")
            out[name] = v.where(has_result.astype(bool), pd.NA)
        out["is_valid_result"] = df["is_valid_result"].astype("boolean")
        out["is_absent"] = df["is_absent"].astype("boolean")
    else:
        out["finish_position"] = pd.array([pd.NA] * len(df), dtype="Int8")
        for name in ("is_win", "is_top2", "is_top3", "is_valid_result", "is_absent"):
            out[name] = pd.array([pd.NA] * len(df), dtype="boolean")

    key_feats = {"entry_number", "stadium_number", "race_number"}
    for c in feats:
        if c in key_feats:
            continue
        out[c] = pd.to_numeric(f[c].astype("Float64"), errors="coerce").astype("float32")
    # 列順: キー → 目的変数 → メタ → 特徴量
    ordered = (fs.KEY_COLUMNS + fs.TARGET_COLUMNS + fs.META_COLUMNS
               + [c for c in feats if c not in fs.KEY_COLUMNS])
    return out[ordered]


def feature_quality(df: pd.DataFrame, variant: str) -> dict:
    feats = fs.features_for(variant)
    return {
        "variant": variant,
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "rows": int(len(df)),
        "races": int(df["race_id"].nunique()),
        "dates": int(df["date"].nunique()),
        "date_min": str(df["date"].min()) if len(df) else None,
        "date_max": str(df["date"].max()) if len(df) else None,
        "valid_rows": int((df["is_valid_result"].fillna(False) & ~df["is_absent"].fillna(False)).sum()),
        "null_rate": {c: round(float(df[c].isna().mean()), 4) for c in feats},
    }


def _write_parquet(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    df.to_parquet(tmp, index=False)
    tmp.replace(path)


def build_training(variant: str, d_from: date, d_to: date, interim_dir: Path = INTERIM_DIR,
                   out_dir: Path = FEATURES_DIR) -> pd.DataFrame:
    """学習用: 期間内の全艇の特徴量を作って保存する。"""
    entries = read_interim("entries", None, d_to, interim_dir)
    races = read_interim("races", None, d_to, interim_dir)
    in_range = (entries["date"] >= d_from) & (entries["date"] <= d_to)
    t_entries = entries[in_range].reset_index(drop=True)
    t_races = races[(races["date"] >= d_from) & (races["date"] <= d_to)].reset_index(drop=True)
    df = make_feature_frame(t_entries, t_races, entries, races, variant, with_target=True)
    _write_parquet(df, out_dir / f"features_{variant}.parquet")
    q = feature_quality(df, variant)
    qpath = out_dir / "_quality" / f"features_{variant}.json"
    qpath.parent.mkdir(parents=True, exist_ok=True)
    qpath.write_text(json.dumps(q, ensure_ascii=False, indent=2), encoding="utf-8")
    return df


def build_today(variant: str, target: date, interim_dir: Path = INTERIM_DIR,
                out_dir: Path = FEATURES_DIR, load_raw: LoadRaw | None = None,
                state=None) -> pd.DataFrame:
    """当日用: 当日の出走表(生JSON)と、前日までの interim から特徴量を作る。目的変数は null。

    state(HistoryState、または状態ファイルのパス)を渡すと、interim の代わりに履歴の状態を使う(F-8)。
    """
    loaded = load_day(target, load_raw or make_loader(), sources=("openapi", "turnmark"))
    if loaded is None:
        raise FileNotFoundError(f"{target} の生データがありません")
    source, raw = loaded
    t = parse.parse_day(raw, source, target)
    if state is not None:
        from src.features.history_state import load_state
        if not hasattr(state, "h_starts"):
            state = load_state(Path(state))
        if state.as_of != target:
            raise ValueError(f"状態の日付 {state.as_of} が対象日 {target} と違います")
        df = make_feature_frame(t["entries"], t["races"], None, None, variant,
                                with_target=False, state=state)
    else:
        # 過去は前日まで(当日分が interim にあっても使わない)
        prev = target - timedelta(days=1)
        entries = read_interim("entries", None, prev, interim_dir)
        races = read_interim("races", None, prev, interim_dir)
        df = make_feature_frame(t["entries"], t["races"], entries, races, variant, with_target=False)
    _write_parquet(df, out_dir / "today" / f"{target.strftime('%Y%m%d')}_{variant}.parquet")
    return df


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="interim から特徴量テーブルを作る")
    p.add_argument("--variant", choices=fs.VARIANTS, default="pre")
    p.add_argument("--from", dest="d_from")
    p.add_argument("--to", dest="d_to")
    p.add_argument("--date", help="当日用。YYYY-MM-DD")
    p.add_argument("--state", help="当日用で、interim の代わりに使う履歴の状態ファイル(history_state)")
    args = p.parse_args(argv)
    try:
        if args.date:
            if args.d_from or args.d_to:
                print("--date と --from/--to は同時に指定できません", file=sys.stderr)
                return 2
            target = date.fromisoformat(args.date)
            df = build_today(args.variant, target, state=args.state)
            print(f"{target}: {df['race_id'].nunique()} レース / {len(df)} 艇")
            return 0
        if not (args.d_from and args.d_to):
            print("--from と --to(または --date)を指定してください", file=sys.stderr)
            return 2
        d_from, d_to = date.fromisoformat(args.d_from), date.fromisoformat(args.d_to)
    except ValueError as e:
        print(f"日付の形式が不正です: {e}", file=sys.stderr)
        return 2
    df = build_training(args.variant, d_from, d_to)
    print(f"features_{args.variant}: {len(df)} 行 / {df['race_id'].nunique()} レース")
    return 0


if __name__ == "__main__":
    sys.exit(main())
