"""展開予想(決まり手の確率)用の特徴量。1行=1レース(F-9。spec/10 3.1節)。

艇ごとの v2 特徴量(make_feature_frame。リーク防止は同じ)を、**予定コース順**に並べ直して
c1_〜c6_ の列にする。予定コースは前日版=枠番、直前版=スタート展示の進入(無ければ枠番)。
同じ予定コースに2艇いる場合(展示の進入の重複など)は、枠番の小さい艇を採り、もう1艇は落とす。

使い方:
    python -m src.features.build_tenkai --variant pre --from 2026-01-01 --to 2026-11-01
        → data/features/tenkai_pre.parquet
    python -m src.features.build_tenkai --variant preview --date 2026-11-06 [--state data/history/state_20261106.parquet]
        → data/features/today/20261106_tenkai_preview.parquet

目的変数: technique_number(1 逃げ 2 差し 3 まくり 4 まくり差し 5 抜き 6 恵まれ)。当日用は null。
学習には is_valid_result=True かつ technique_number が null でないレースを使う。
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from src.features import feature_sets as fs
from src.features import parse
from src.features.build_features import FEATURES_DIR, _write_parquet, make_feature_frame
from src.features.build_interim import INTERIM_DIR, read_interim
from src.features.raw_source import LoadRaw, load_day, make_loader


def boat_variant(variant: str) -> str:
    if variant not in fs.TENKAI_VARIANTS:
        raise ValueError(f"未知の variant です: {variant}")
    return f"{variant}_v2"


def to_race_rows(boats: pd.DataFrame, races: pd.DataFrame, variant: str,
                 with_target: bool = True) -> pd.DataFrame:
    """艇ごとの v2 特徴量(1行=1艇)を、1行=1レースに並べ直す。"""
    b = boats.copy()
    planned = b["entry_number"].astype("float32")
    if variant == "preview":
        planned = b["pv_course_number"].fillna(planned)
    b["_course"] = planned.astype(int)
    b = (b[b["_course"].between(1, 6)]
         .sort_values(["race_id", "_course", "entry_number"])
         .drop_duplicates(["race_id", "_course"]))
    per_boat = fs.tenkai_boat_columns(variant)
    wide = b.pivot(index="race_id", columns="_course", values=per_boat)
    wide = wide.reindex(columns=pd.MultiIndex.from_product([per_boat, range(1, 7)]))
    wide.columns = [f"c{c}_{name}" for name, c in wide.columns]

    race_cols = fs.tenkai_race_columns(variant)
    first = boats.sort_values(["race_id", "entry_number"]).drop_duplicates("race_id").set_index("race_id")
    extra = [c for c in race_cols if c not in fs.TENKAI_KEY_COLUMNS]
    out = first[["date", "stadium_number", "race_number"]].join(first[extra].astype("float32"))
    out = out.join(wide)
    out.index.name = "race_id"
    out = out.reset_index()
    out["race_id"] = out["race_id"].astype("string")

    if with_target:
        r = races.set_index("race_id")
        out["technique_number"] = out["race_id"].map(r["technique_number"]).astype("Int8")
        out["is_valid_result"] = out["race_id"].map(r["is_valid_result"]).astype("boolean")
    else:
        out["technique_number"] = pd.array([pd.NA] * len(out), dtype="Int8")
        out["is_valid_result"] = pd.array([pd.NA] * len(out), dtype="boolean")
    feats = fs.tenkai_features_for(variant)
    for c in feats:
        if c not in ("stadium_number", "race_number"):
            out[c] = out[c].astype("float32")
    cols = fs.TENKAI_KEY_COLUMNS + fs.TENKAI_TARGET_COLUMNS + [
        c for c in feats if c not in fs.TENKAI_KEY_COLUMNS]
    return out[cols].sort_values(["date", "stadium_number", "race_number"]).reset_index(drop=True)


def build_training(variant: str, d_from: date, d_to: date, interim_dir: Path = INTERIM_DIR,
                   out_dir: Path = FEATURES_DIR) -> pd.DataFrame:
    entries = read_interim("entries", None, d_to, interim_dir)
    races = read_interim("races", None, d_to, interim_dir)
    t_e = entries[(entries["date"] >= d_from) & (entries["date"] <= d_to)].reset_index(drop=True)
    t_r = races[(races["date"] >= d_from) & (races["date"] <= d_to)].reset_index(drop=True)
    boats = make_feature_frame(t_e, t_r, entries, races, boat_variant(variant), with_target=False)
    df = to_race_rows(boats, t_r, variant, with_target=True)
    _write_parquet(df, out_dir / f"tenkai_{variant}.parquet")
    return df


def build_today(variant: str, target: date, interim_dir: Path = INTERIM_DIR,
                out_dir: Path = FEATURES_DIR, load_raw: LoadRaw | None = None,
                state=None) -> pd.DataFrame:
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
        boats = make_feature_frame(t["entries"], t["races"], None, None, boat_variant(variant),
                                   with_target=False, state=state)
    else:
        prev = target - timedelta(days=1)
        boats = make_feature_frame(t["entries"], t["races"], read_interim("entries", None, prev, interim_dir),
                                   read_interim("races", None, prev, interim_dir), boat_variant(variant),
                                   with_target=False)
    df = to_race_rows(boats, t["races"], variant, with_target=False)
    _write_parquet(df, out_dir / "today" / f"{target.strftime('%Y%m%d')}_tenkai_{variant}.parquet")
    return df


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="展開予想用の特徴量(1行=1レース)を作る")
    p.add_argument("--variant", choices=fs.TENKAI_VARIANTS, default="pre")
    p.add_argument("--from", dest="d_from")
    p.add_argument("--to", dest="d_to")
    p.add_argument("--date")
    p.add_argument("--state")
    a = p.parse_args(argv)
    if a.date:
        df = build_today(a.variant, date.fromisoformat(a.date), state=a.state)
    elif a.d_from and a.d_to:
        df = build_training(a.variant, date.fromisoformat(a.d_from), date.fromisoformat(a.d_to))
    else:
        print("--from と --to(または --date)を指定してください", file=sys.stderr)
        return 2
    print(f"tenkai_{a.variant}: {len(df)} レース")
    return 0


if __name__ == "__main__":
    sys.exit(main())
