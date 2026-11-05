"""A3 ランキング(F-13)のテスト。D−1 までの結果だけで作られていること(リーク)。"""

from __future__ import annotations

from datetime import timedelta

import pandas as pd
import pytest

from features_testlib import DAY1, DAY2, load_fixture, write_days, write_raw
from src.common import analytics_rules as ar
from src.features.analytics.rankings import COLUMNS, SERIES_MIN_STARTS, TOP_N, build_day
from src.features.build_interim import build_interim, read_interim
from src.features.history_state import build_state
from src.features.raw_source import make_loader

DAYS = [DAY1 - timedelta(days=i) for i in range(40)]  # 40日分(件数を下限以上にする)


def _setup(tmp_path, day2_raw=None, day1_raw=None):
    raw_dir = tmp_path / "raw"
    write_days(raw_dir, DAYS[1:])
    write_days(raw_dir, [DAY1], raw=day1_raw or load_fixture("turnmark", DAY1))
    write_raw(raw_dir, "openapi", DAY2, day2_raw or load_fixture("openapi", DAY2))
    out = tmp_path / "interim"
    build_interim(min(DAYS), DAY2, load_raw=make_loader(raw_dir), out_dir=out, verbose=False)
    return raw_dir, out


def _shuffle(raw):
    for st in raw["programs"]["stadiums"].values():
        for race in st["races"].values():
            rs = race["result"]["racers"]
            keys = sorted(rs)
            vals = [rs[k]["place_number"] for k in keys][::-1]
            for k, p in zip(keys, vals):
                rs[k]["place_number"] = p
                rs[k]["start_timing"] = 0.01
            if race["result"].get("technique_number") is not None:
                race["result"]["technique_number"] = 3
    return raw


@pytest.fixture
def ranked(tmp_path):
    raw_dir, interim = _setup(tmp_path / "a")
    return build_day(DAY2, interim_dir=interim, out_dir=tmp_path / "out", load_raw=make_loader(raw_dir)), raw_dir, interim


def test_structure(tmp_path, ranked):
    r, _, _ = ranked
    assert list(r.columns) == COLUMNS
    assert (tmp_path / "out" / "rankings" / "20261102.parquet").exists()
    assert (r.groupby(["ranking", "scope"]).size() <= TOP_N).all()
    assert set(r["ranking"]) >= {"nige", "avg_st", "sashi", "makuri", "motor", "series"}
    # 件数の下限
    rate = r[r["ranking"].isin(["nige", "avg_st", "sashi", "makuri"])]
    assert (rate["n"] >= ar.MIN_COUNT_SHOW).all()
    assert (r.loc[r["ranking"] == "series", "n"] >= SERIES_MIN_STARTS).all()
    # 逃げ: 今日1号艇の選手だけ。5242 は過去40走すべて1コース
    nige = r[(r["ranking"] == "nige") & (r["scope"] == 0)]
    assert (nige["entry_number"] == 1).all() and 5242 in set(nige["racer_number"])
    assert nige.set_index("racer_number").loc[5242, "n"] == 2 * 40 - 0  # 1R・5R × 40日
    # 平均STは小さい順
    st = r[(r["ranking"] == "avg_st") & (r["scope"] == 0)]["value"]
    assert st.is_monotonic_increasing
    # 場ごとと全場(0)
    assert set(r.loc[r["ranking"] == "nige", "scope"]) >= {0, 2}
    assert 0 not in set(r.loc[r["ranking"].isin(["motor", "series"]), "scope"])


def test_leak_same_day_results(tmp_path, ranked):
    """D 日の結果を変えても、ランキングは変わらない。"""
    r, _, _ = ranked
    raw_dir, interim = _setup(tmp_path / "b", day2_raw=_shuffle(load_fixture("openapi", DAY2)))
    # D 日の結果は interim には入っているが、使われない
    e = read_interim("entries", interim_dir=interim)
    assert e.loc[e["date"] == DAY2, "place_number"].notna().any()
    r2 = build_day(DAY2, interim_dir=interim, out_dir=tmp_path / "o2", load_raw=make_loader(raw_dir))
    pd.testing.assert_frame_equal(r, r2)


def test_previous_day_results_matter(tmp_path, ranked):
    """D−1 日の結果を変えると、値が変わる(テストの感度の確認)。"""
    r, _, _ = ranked
    raw_dir, interim = _setup(tmp_path / "c", day1_raw=_shuffle(load_fixture("turnmark", DAY1)))
    r2 = build_day(DAY2, interim_dir=interim, out_dir=tmp_path / "o3", load_raw=make_loader(raw_dir))
    assert not r.equals(r2)


def test_state_gives_same(tmp_path, ranked):
    r, raw_dir, interim = ranked
    st = build_state(read_interim("entries", interim_dir=interim), read_interim("races", interim_dir=interim), DAY2)
    r2 = build_day(DAY2, out_dir=tmp_path / "o4", load_raw=make_loader(raw_dir), state=st)
    pd.testing.assert_frame_equal(r, r2)
