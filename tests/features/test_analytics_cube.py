"""A2 キューブ(F-12)のテスト。"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from features_testlib import DAY1, write_days
from src.common import analytics_rules as ar
from src.features.analytics.base import load_base
from src.features.analytics.cube import ALL, COLUMNS, CUTS, build_cubes, main
from src.features.build_interim import build_interim
from src.features.raw_source import make_loader


def _interim(tmp_path, days):
    raw_dir = tmp_path / "raw"
    write_days(raw_dir, days)
    out = tmp_path / "interim"
    build_interim(min(days), max(days), load_raw=make_loader(raw_dir), out_dir=out, verbose=False)
    return out


@pytest.fixture
def cube(tmp_path):
    days = [DAY1 - timedelta(days=i) for i in range(40)]  # 1日 有効2レース × 40日
    return build_cubes(load_base(_interim(tmp_path, days)))


def test_structure(cube):
    assert set(cube) == {2}
    c = cube[2]
    assert list(c.columns) == COLUMNS
    assert set(c["cut"]) == set(CUTS)
    assert set(c["period"]) == {"all"}  # データが3年未満
    # 切り口で使わない次元は all
    course = c[c["cut"] == "course"]
    assert len(course) == 6 and (course[["wind", "rank", "grade", "season"]] == ALL).all().all()
    cw = c[c["cut"] == "course_wind"]
    assert (cw["rank"] == ALL).all() and set(cw["wind"]) <= set(ar.WIND_BUCKETS)
    assert set(c.loc[c["cut"] == "course_rank", "rank"]) <= {"A1", "A2", "B1", "B2"}
    assert set(c.loc[c["cut"] == "course_grade", "grade"]) <= set(ar.GRADE_BUCKETS)
    assert not [x for x in c.columns if "odds" in x]


def test_values_and_masking(cube):
    c = cube[2]
    row = c[(c["cut"] == "course") & (c["course"] == "2")].iloc[0]
    # 2コース: 1R で1着(差し)、4R で5着。40日分
    assert row["n_starts"] == 80 and row["n_races"] == 80
    assert row["win_rate"] == pytest.approx(0.5)
    assert row["share_sashi"] == pytest.approx(1.0)
    assert row["level"] == ar.SAMPLE_FEW
    # 件数の合計は切り口によらず同じ
    assert c.groupby("cut")["n_starts"].sum().nunique() <= 2  # 風・級別の欠損で少し減る場合がある
    assert c[c["cut"] == "course"]["n_starts"].sum() == c[c["cut"] == "course_season"]["n_starts"].sum()


def test_masking_small_counts(tmp_path):
    """出走数が30未満の行は、率・割合・払戻を入れず件数だけ。"""
    c = build_cubes(load_base(_interim(tmp_path, [DAY1 - timedelta(days=i) for i in range(10)])))[2]
    low = c[c["n_starts"] < ar.MIN_COUNT_SHOW]
    assert len(low) == len(c)  # 1日2レース × 10日 = 20走
    assert low[["win_rate", "share_nige", "trifecta_mean"]].isna().all().all()
    assert (low["level"] == ar.SAMPLE_INSUFFICIENT).all() and (low["n_starts"] > 0).all()


def test_recent_period_only_with_three_years(tmp_path):
    days = [date(2023, 6, 1) + timedelta(days=90 * i) for i in range(16)] + [DAY1]
    cubes = build_cubes(load_base(_interim(tmp_path, days)))
    c = cubes[2]
    assert set(c["period"]) == {"all", "3y"}
    a = c[(c["period"] == "all") & (c["cut"] == "course")]["n_starts"].sum()
    b = c[(c["period"] == "3y") & (c["cut"] == "course")]["n_starts"].sum()
    assert a > b > 0


def test_cli(tmp_path):
    interim = _interim(tmp_path, [DAY1])
    assert main([], interim_dir=interim, out_dir=tmp_path / "o") == 0
    assert len(pd.read_parquet(tmp_path / "o" / "cube" / "02.parquet")) > 0
