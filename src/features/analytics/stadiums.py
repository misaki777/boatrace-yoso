"""A1 場の傾向(F-11。spec/11 2.1)。E-15 が docs/data/analytics/stadiums.json に出す。

出力(data/features/analytics/stadiums/。stadium_number=0 は全国):
- course.parquet     : period, stadium_number, course, n, win_rate, win_lo, win_hi, top2_rate, top3_rate, level
- condition.parquet  : period, stadium_number, cond_type(all/season/night/wind), cond, n_races,
                       c1_n, c1_win_rate, c1_win_lo, c1_win_hi, share_{nige..megumare}, level
                       (night はナイターのレースがある場だけ。cond は night / day)
- summary.parquet    : period, stadium_number, date_from, date_to, n_races, trifecta_mean, trifecta_median,
                       manshu_rate, manshu_lo, manshu_hi, boat1_win_rate, c1_win_rate,
                       c1_win_diff_national, manshu_diff_national, level

- period: all(データの全期間)と 365d(直近365日)。期間はデータから読む(決め打ちしない)。
- 件数が下限(30)未満の行は率を入れない(件数だけ)。level は normal / reference / insufficient。
- 払戻は払戻金(オッズの数値は使わない)。本番の気象(result)を使う(過去の傾向の集計なのでリークの問題は無い)。

使い方:
    python -m src.features.analytics.stadiums [--from YYYY-MM-DD] [--to YYYY-MM-DD]
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

import pandas as pd

from src.features.analytics.base import (
    ANALYTICS_DIR, TECHNIQUES, AnalyticsBase, in_period, load_base, periods, safe_rate, write_parquet,
)
from src.features.analytics.rules import count_level, mask_rates, wilson
from src.features.build_interim import INTERIM_DIR

NATIONAL = 0
SHARE_COLS = [f"share_{t}" for t in TECHNIQUES.values()]


def _with_national(df: pd.DataFrame) -> pd.DataFrame:
    """各場の行に、全国(stadium_number=0)の行を足す。"""
    return pd.concat([df, df.assign(stadium_number=NATIONAL)], ignore_index=True)


def course_table(boats: pd.DataFrame) -> pd.DataFrame:
    b = _with_national(boats[boats["course"].between(1, 6)])
    g = b.groupby(["stadium_number", "course"])[["win", "top2", "top3"]].agg(["sum", "count"])
    out = pd.DataFrame({
        "stadium_number": g.index.get_level_values(0), "course": g.index.get_level_values(1).astype(int),
        "n": g[("win", "count")].values,
        "win_rate": (g[("win", "sum")] / g[("win", "count")]).values,
        "top2_rate": (g[("top2", "sum")] / g[("win", "count")]).values,
        "top3_rate": (g[("top3", "sum")] / g[("win", "count")]).values,
    })
    lo, hi = wilson(g[("win", "sum")].values, g[("win", "count")].values)
    out["win_lo"], out["win_hi"] = lo.values, hi.values
    out["level"] = count_level(out["n"]).values
    out = mask_rates(out, "n", ["win_rate", "win_lo", "win_hi", "top2_rate", "top3_rate"])
    return out[["stadium_number", "course", "n", "win_rate", "win_lo", "win_hi", "top2_rate", "top3_rate", "level"]]


def _cond_agg(races: pd.DataFrame, cond_type: str, cond: pd.Series) -> pd.DataFrame:
    r = races.assign(cond=cond.values)
    r = r[r["cond"].notna()]
    rows = []
    for (sn, c), g in r.groupby(["stadium_number", "cond"]):
        tech = g["technique"].dropna()
        c1 = g["c1_win"].dropna()
        row = {"stadium_number": sn, "cond_type": cond_type, "cond": str(c), "n_races": len(g),
               "c1_n": len(c1), "c1_k": c1.sum(), "tech_n": len(tech)}
        for t in TECHNIQUES.values():
            row[f"share_{t}"] = (tech == t).mean() if len(tech) else float("nan")
        rows.append(row)
    return pd.DataFrame(rows)


def condition_table(races: pd.DataFrame) -> pd.DataFrame:
    r = _with_national(races)
    night_stadiums = set(races.loc[races["is_night"], "stadium_number"])
    parts = [
        _cond_agg(r, "all", pd.Series("all", index=r.index)),
        _cond_agg(r, "season", r["season"]),
        # ナイターはナイター開催のある場だけ(全国の行は全場のナイター/昼の比較)
        _cond_agg(r[r["stadium_number"].isin(night_stadiums | {NATIONAL})], "night",
                  r.loc[r["stadium_number"].isin(night_stadiums | {NATIONAL}), "is_night"]
                  .map({True: "night", False: "day"})),
        _cond_agg(r, "wind", r["wind"]),
    ]
    out = pd.concat([p for p in parts if len(p)], ignore_index=True)
    out["c1_win_rate"] = safe_rate(out["c1_k"], out["c1_n"])
    lo, hi = wilson(out["c1_k"], out["c1_n"])
    out["c1_win_lo"], out["c1_win_hi"] = lo, hi
    out["level"] = count_level(out["n_races"])
    out = mask_rates(out, "n_races", ["c1_win_rate", "c1_win_lo", "c1_win_hi"] + SHARE_COLS)
    return out[["stadium_number", "cond_type", "cond", "n_races", "c1_n", "c1_win_rate", "c1_win_lo",
                "c1_win_hi", *SHARE_COLS, "level"]]


def summary_table(races: pd.DataFrame, rng: tuple[date, date]) -> pd.DataFrame:
    r = _with_national(races)
    g = r.groupby("stadium_number")
    out = pd.DataFrame({
        "n_races": g.size(),
        "trifecta_mean": g["trifecta_amount"].mean(),
        "trifecta_median": g["trifecta_amount"].median(),
        "manshu_k": g["manshu"].sum(), "manshu_n": g["manshu"].count(),
        "boat1_win_rate": g["boat1_win"].mean(),
        "c1_win_rate": g["c1_win"].mean(),
    }).reset_index()
    out["manshu_rate"] = safe_rate(out["manshu_k"], out["manshu_n"])
    lo, hi = wilson(out["manshu_k"], out["manshu_n"])
    out["manshu_lo"], out["manshu_hi"] = lo, hi
    nat = out.set_index("stadium_number").loc[NATIONAL]
    out["c1_win_diff_national"] = out["c1_win_rate"] - nat["c1_win_rate"]
    out["manshu_diff_national"] = out["manshu_rate"] - nat["manshu_rate"]
    out["level"] = count_level(out["n_races"])
    out = mask_rates(out, "n_races", ["trifecta_mean", "trifecta_median", "manshu_rate", "manshu_lo",
                                      "manshu_hi", "boat1_win_rate", "c1_win_rate",
                                      "c1_win_diff_national", "manshu_diff_national"])
    out["date_from"], out["date_to"] = rng
    return out[["stadium_number", "date_from", "date_to", "n_races", "trifecta_mean", "trifecta_median",
                "manshu_rate", "manshu_lo", "manshu_hi", "boat1_win_rate", "c1_win_rate",
                "c1_win_diff_national", "manshu_diff_national", "level"]]


def build_stadiums(base: AnalyticsBase) -> dict[str, pd.DataFrame]:
    out: dict[str, list[pd.DataFrame]] = {"course": [], "condition": [], "summary": []}
    for name, rng in periods(base, 365).items():
        races, boats = in_period(base.races, rng), in_period(base.boats, rng)
        out["course"].append(course_table(boats).assign(period=name))
        out["condition"].append(condition_table(races).assign(period=name))
        out["summary"].append(summary_table(races, rng).assign(period=name))
    res = {}
    for k, parts in out.items():
        df = pd.concat(parts, ignore_index=True)
        res[k] = df[["period"] + [c for c in df.columns if c != "period"]]
    return res


def main(argv: list[str] | None = None, interim_dir: Path = INTERIM_DIR,
         out_dir: Path = ANALYTICS_DIR) -> int:
    p = argparse.ArgumentParser(description="A1 場の傾向")
    p.add_argument("--from", dest="d_from")
    p.add_argument("--to", dest="d_to")
    a = p.parse_args(argv)
    base = load_base(interim_dir, date.fromisoformat(a.d_from) if a.d_from else None,
                     date.fromisoformat(a.d_to) if a.d_to else None)
    tables = build_stadiums(base)
    for k, df in tables.items():
        write_parquet(df, out_dir / "stadiums" / f"{k}.parquet")
    print(f"場の傾向: {base.date_from}〜{base.date_to} レース {len(base.races)} "
          + " ".join(f"{k}={len(v)}行" for k, v in tables.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
