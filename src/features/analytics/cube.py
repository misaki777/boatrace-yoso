"""A2 条件で集計(事前計算のキューブ。F-12。spec/11 2.2)。

出力: data/features/analytics/cube/{stadium_number}.parquet(場ごと。E-15 が cube/{stadium}.json に出す)
  1行 = 期間 × 切り口 × 区分。列:
    period(all / 3y。データが3年未満の間は all だけ), cut, course(1〜6), wind, rank, grade, season,
    n_starts, n_races, win_rate, win_lo, win_hi, top2_rate, top3_rate,
    share_{nige..megumare}(その条件の艇が1着になったときの決まり手の割合),
    trifecta_mean, trifecta_median(その条件の艇が出たレースの3連単の払戻金), level
  - 切り口(cut): course / course_wind / course_rank / course_grade / course_season / course_wind_rank。
    切り口で使わない次元は "all"。
  - 区分は共通ルール(src/common/analytics_rules.py)の風10区分・グレード3区分・季節。級別は A1/A2/B1/B2。
  - コースは本番の進入、風は本番の気象(過去の傾向の集計なのでリークの問題は無い)。
  - 出走数が下限(30)未満の行は、率・割合・払戻を入れず件数だけ。行そのものは残す(件数0の組合せは作らない)。
  - オッズは使わない(払戻金だけ)。

使い方:
    python -m src.features.analytics.cube [--from YYYY-MM-DD] [--to YYYY-MM-DD]
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

import pandas as pd

from src.common import codes
from src.features.analytics.base import (
    ANALYTICS_DIR, TECHNIQUES, AnalyticsBase, in_period, load_base, periods, safe_rate, write_parquet,
)
from src.features.analytics.rules import count_level, mask_rates, wilson
from src.features.build_interim import INTERIM_DIR

ALL = "all"
DIMS = ["course", "wind", "rank", "grade", "season"]
CUTS: dict[str, list[str]] = {
    "course": ["course"],
    "course_wind": ["course", "wind"],
    "course_rank": ["course", "rank"],
    "course_grade": ["course", "grade"],
    "course_season": ["course", "season"],
    "course_wind_rank": ["course", "wind", "rank"],
}
RECENT_YEARS_DAYS = 365 * 3
SHARE_COLS = [f"share_{t}" for t in TECHNIQUES.values()]
RATE_COLS = ["win_rate", "win_lo", "win_hi", "top2_rate", "top3_rate", *SHARE_COLS,
             "trifecta_mean", "trifecta_median"]
COLUMNS = ["period", "cut", *DIMS, "n_starts", "n_races", *RATE_COLS, "level"]


def _prepare(boats: pd.DataFrame) -> pd.DataFrame:
    b = boats[boats["course"].between(1, 6)].copy()
    b["course"] = b["course"].astype(int).astype(str)
    b["rank"] = b["rank_number"].map(lambda r: codes.RANK_NAMES.get(int(r)) if pd.notna(r) else None)
    return b


def _aggregate(b: pd.DataFrame, cut: str, keys: list[str]) -> pd.DataFrame:
    b = b.dropna(subset=keys)
    if b.empty:
        return pd.DataFrame(columns=COLUMNS)
    g = b.groupby(keys, sort=True)
    out = g.agg(n_starts=("win", "size"), n_races=("race_id", "nunique"), wins=("win", "sum"),
                top2=("top2", "sum"), top3=("top3", "sum")).reset_index()
    out["win_rate"] = safe_rate(out["wins"], out["n_starts"])
    out["top2_rate"] = safe_rate(out["top2"], out["n_starts"])
    out["top3_rate"] = safe_rate(out["top3"], out["n_starts"])
    lo, hi = wilson(out["wins"], out["n_starts"])
    out["win_lo"], out["win_hi"] = lo.values, hi.values
    # 1着になったときの決まり手の割合
    winners = b[(b["win"] == 1) & b["technique"].notna()]
    tech = pd.crosstab([winners[k] for k in keys], winners["technique"], normalize="index")
    tech = tech.reindex(columns=list(TECHNIQUES.values()), fill_value=0.0).add_prefix("share_")
    out = out.merge(tech.reset_index(), on=keys, how="left")
    for c in SHARE_COLS:  # 1着が1回も無い区分は割合を出さない
        out[c] = out[c].where(out["wins"] > 0)
    # 払戻はレース単位(同じレースを重ねて数えない)
    races = b.drop_duplicates(keys + ["race_id"])
    pay = races.groupby(keys)["trifecta_amount"].agg(trifecta_mean="mean", trifecta_median="median").reset_index()
    out = out.merge(pay, on=keys, how="left")
    out["cut"] = cut
    for d in DIMS:
        if d not in keys:
            out[d] = ALL
    out["level"] = count_level(out["n_starts"]).values
    out = mask_rates(out, "n_starts", RATE_COLS)
    return out[[c for c in COLUMNS if c != "period"]]


def build_cube_for_stadium(boats: pd.DataFrame) -> pd.DataFrame:
    b = _prepare(boats)
    return pd.concat([_aggregate(b, cut, keys) for cut, keys in CUTS.items()], ignore_index=True)


def build_cubes(base: AnalyticsBase) -> dict[int, pd.DataFrame]:
    out: dict[int, list[pd.DataFrame]] = {}
    for name, rng in periods(base, RECENT_YEARS_DAYS, min_span_days=RECENT_YEARS_DAYS).items():
        name = "3y" if name != "all" else name
        boats = in_period(base.boats, rng)
        for sn, g in boats.groupby("stadium_number"):
            out.setdefault(int(sn), []).append(build_cube_for_stadium(g).assign(period=name))
    res = {}
    for sn, parts in out.items():
        df = pd.concat(parts, ignore_index=True)[COLUMNS]
        for c in ("n_starts", "n_races"):
            df[c] = df[c].astype("int64")
        res[sn] = df
    return res


def main(argv: list[str] | None = None, interim_dir: Path = INTERIM_DIR,
         out_dir: Path = ANALYTICS_DIR) -> int:
    p = argparse.ArgumentParser(description="A2 条件で集計(キューブ)")
    p.add_argument("--from", dest="d_from")
    p.add_argument("--to", dest="d_to")
    a = p.parse_args(argv)
    base = load_base(interim_dir, date.fromisoformat(a.d_from) if a.d_from else None,
                     date.fromisoformat(a.d_to) if a.d_to else None)
    cubes = build_cubes(base)
    for sn, df in cubes.items():
        write_parquet(df, out_dir / "cube" / f"{sn:02d}.parquet")
    sizes = [len(d) for d in cubes.values()]
    print(f"キューブ: {base.date_from}〜{base.date_to} {len(cubes)}場 "
          f"1場あたり {min(sizes)}〜{max(sizes)}行")
    return 0


if __name__ == "__main__":
    sys.exit(main())
