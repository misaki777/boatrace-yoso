"""分析の集計の材料(interim から、1行=1レース と 1行=1艇 の表を作る)。期間はデータから読む。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from src.common import analytics_rules as ar
from src.features.build_interim import INTERIM_DIR, read_interim

ROOT = Path(__file__).resolve().parents[3]
ANALYTICS_DIR = ROOT / "data" / "features" / "analytics"

TECHNIQUES = {1: "nige", 2: "sashi", 3: "makuri", 4: "makurisashi", 5: "nuki", 6: "megumare"}


@dataclass
class AnalyticsBase:
    races: pd.DataFrame   # 1行=1レース(有効な結果のあるレースだけ)
    boats: pd.DataFrame   # 1行=1艇(上のレースの、欠場を除く艇)
    date_from: date
    date_to: date


def load_base(interim_dir: Path = INTERIM_DIR, d_from: date | None = None,
              d_to: date | None = None) -> AnalyticsBase:
    """interim から材料を作る。d_from / d_to を省略するとデータの最初〜最後の日(結果のある日)。"""
    races = read_interim("races", d_from, d_to, interim_dir)
    entries = read_interim("entries", d_from, d_to, interim_dir)
    payouts = read_interim("payouts", d_from, d_to, interim_dir)
    races = races[races["is_valid_result"].fillna(False).astype(bool)].copy()
    if races.empty:
        raise ValueError("有効な結果のあるレースがありません")

    e = entries[entries["race_id"].isin(races["race_id"]) & ~entries["is_absent"].fillna(False).astype(bool)]
    fp = e["finish_position"].astype("Float64")
    boats = pd.DataFrame({
        "race_id": e["race_id"].values,
        "course": e["rs_course_number"].astype("Float64").values,
        "entry_number": e["entry_number"].astype("int64").values,
        "racer_number": e["racer_number"].astype("Float64").values,
        "rank_number": e["rank_number"].astype("Float64").values,
        "win": (fp == 1).fillna(False).astype(float).values,
        "top2": (fp <= 2).fillna(False).astype(float).values,
        "top3": (fp <= 3).fillna(False).astype(float).values,
    })

    c1 = boats[boats["course"] == 1].groupby("race_id")["win"].max()
    b1 = boats[boats["entry_number"] == 1].groupby("race_id")["win"].max()
    tri = payouts[(payouts["bet_type"] == "trifecta") & payouts["amount"].notna()]
    tri_amount = tri.groupby("race_id")["amount"].mean()

    closed = pd.to_datetime(races["closed_at"])
    r = pd.DataFrame({
        "race_id": races["race_id"].values,
        "date": races["date"].values,
        "stadium_number": races["stadium_number"].astype("int64").values,
        "technique": races["technique_number"].astype("Float64").map(
            lambda t: TECHNIQUES.get(int(t)) if pd.notna(t) else None).values,
        "is_night": [bool(ar.is_night(t.to_pydatetime())) if pd.notna(t) else False for t in closed],
        "season": [ar.season_of(d) for d in races["date"]],
        # 本番の気象(result)。風の区分は共通ルールの10区分
        "wind": [ar.wind_bucket(None if pd.isna(d) else int(d), None if pd.isna(s) else float(s))
                 for d, s in zip(races["rs_wind_direction_number"], races["rs_wind_speed"])],
        "grade": [ar.grade_bucket(None if pd.isna(g) else int(g)) for g in races["grade_number"]],
    })
    r["c1_win"] = r["race_id"].map(c1)
    r["boat1_win"] = r["race_id"].map(b1)
    r["trifecta_amount"] = r["race_id"].map(tri_amount)
    r["manshu"] = [float(ar.is_manshu(int(a))) if pd.notna(a) else np.nan for a in r["trifecta_amount"]]

    # 艇の表にレースの条件を付ける(F-12 のキューブ用)
    boats = boats.merge(r[["race_id", "date", "stadium_number", "technique", "season", "wind", "grade",
                           "trifecta_amount"]], on="race_id", how="left")
    return AnalyticsBase(races=r, boats=boats, date_from=min(r["date"]), date_to=max(r["date"]))


def periods(base: AnalyticsBase, recent_days: int | None = 365,
            min_span_days: int | None = None) -> dict[str, tuple[date, date]]:
    """集計する期間。all(データの全期間)と、直近 recent_days 日。

    min_span_days を渡すと、データの期間がそれより短いときは直近の期間を出さない(spec 2.2 の直近3年)。
    """
    out = {"all": (base.date_from, base.date_to)}
    if recent_days:
        span = (base.date_to - base.date_from).days + 1
        if min_span_days is None or span >= min_span_days:
            out[f"{recent_days}d"] = (base.date_to - timedelta(days=recent_days - 1), base.date_to)
    return out


def in_period(df: pd.DataFrame, rng: tuple[date, date]) -> pd.DataFrame:
    return df[(df["date"] >= rng[0]) & (df["date"] <= rng[1])]


def write_parquet(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    df.to_parquet(tmp, index=False)
    tmp.replace(path)


def safe_rate(k: pd.Series, n: pd.Series) -> pd.Series:
    return (k / n.where(n > 0)).astype(float)



