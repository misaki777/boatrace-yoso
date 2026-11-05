"""履歴の状態(F-8)のテスト。状態から作った当日の特徴量が、全履歴から作ったものと一致すること。"""

from __future__ import annotations

import copy
from datetime import date, timedelta

import pandas as pd
import pytest

from features_testlib import DAY1, DAY2, load_fixture, write_raw
from src.features import feature_sets as fs
from src.features.build_features import build_today, build_training
from src.features.build_interim import build_interim, read_interim
from src.features.history_state import (
    KEEP_DAYS, _dn, advance_state, build_state, load_state, save_state,
)
from src.features.raw_source import make_loader

# 窓の境目をまたぐ日付に、1日目の内容を置く(対象日は DAY2 = 2026-11-02)
HIST_DATES = [
    date(2025, 7, 1),                        # 古い(畳まれる)
    DAY2 - timedelta(days=KEEP_DAYS + 1),    # D−366: 畳まれる最後の日
    DAY2 - timedelta(days=KEEP_DAYS),        # D−365: 365日の窓の最初の日
    DAY2 - timedelta(days=181),
    DAY2 - timedelta(days=180),              # 180日の窓の最初の日
    DAY2 - timedelta(days=91),
    DAY2 - timedelta(days=90),               # 90日の窓の最初の日
    date(2026, 10, 31),                      # 節が 10/31〜11/02 につながる
]


def _shift(raw: dict, d: date) -> dict:
    raw = copy.deepcopy(raw)
    ymd = d.isoformat()
    for st in raw["programs"]["stadiums"].values():
        for race in st["races"].values():
            race["date"] = ymd
            if race.get("closed_at"):
                race["closed_at"] = ymd + race["closed_at"][10:]
            for k in ("preview", "result", "odds"):
                if isinstance(race.get(k), dict):
                    race[k]["date"] = ymd
    return raw


@pytest.fixture
def long_interim(tmp_path):
    raw_dir = tmp_path / "raw"
    day1 = load_fixture("turnmark", DAY1)
    for i, d in enumerate(HIST_DATES):
        r = _shift(day1, d)
        if i % 2:  # 日によって結果を少し変える(同じ値ばかりだと畳み方の誤りを見逃すため)
            for st in r["programs"]["stadiums"].values():
                for race in st["races"].values():
                    rs = race["result"]["racers"]
                    if rs["1"]["place_number"] and rs["2"]["place_number"]:
                        rs["1"]["place_number"], rs["2"]["place_number"] = rs["2"]["place_number"], rs["1"]["place_number"]
                    race["result"]["technique_number"] = 3 if race["result"]["technique_number"] else None
        write_raw(raw_dir, "turnmark", d, r)
    write_raw(raw_dir, "turnmark", DAY1, day1)
    write_raw(raw_dir, "openapi", DAY2, load_fixture("openapi", DAY2))
    out = tmp_path / "interim"
    build_interim(HIST_DATES[0], DAY2, load_raw=make_loader(raw_dir), out_dir=out, verbose=False)
    return raw_dir, out


@pytest.mark.parametrize("variant", fs.VARIANTS)
@pytest.mark.parametrize("target", [DAY1, DAY2])
def test_state_matches_full_history(tmp_path, long_interim, variant, target):
    raw_dir, interim = long_interim
    full = build_training(variant, HIST_DATES[0], target, interim_dir=interim, out_dir=tmp_path / "full")
    entries, races = read_interim("entries", interim_dir=interim), read_interim("races", interim_dir=interim)
    state = build_state(entries, races, target)
    # 古い行が実際に畳まれていること(テストが空振りしていないことの確認)
    old = state.h_starts["dn"] < _dn(target) - KEEP_DAYS
    assert old.any() and state.h_starts.loc[old, "n"].max() > 1  # 複数の出走が1行になっている
    path = save_state(state, tmp_path / "state.parquet")
    today = build_today(variant, target, out_dir=tmp_path / "t", load_raw=make_loader(raw_dir),
                        state=path)
    feats = fs.features_for(variant)
    a = full[full["date"] == target].reset_index(drop=True)
    assert len(a) == len(today) > 0
    pd.testing.assert_frame_equal(a[fs.KEY_COLUMNS + feats], today[fs.KEY_COLUMNS + feats])
    # H 特徴量に値が入っている(履歴がある)ことも確認
    assert (today["h_racer_n_90d"] > 0).any()


def test_advance_matches_build(tmp_path, long_interim):
    """毎日1日ずつ進めた状態は、全履歴から直接作った状態と同じ特徴量になる。"""
    raw_dir, interim = long_interim
    entries, races = read_interim("entries", interim_dir=interim), read_interim("races", interim_dir=interim)
    start = HIST_DATES[2]  # D−365 から進める(途中で窓の外へ出る日・畳まれる日を通る)
    st = build_state(entries, races, start)
    st = load_state(save_state(st, tmp_path / "s0.parquet"))
    while st.as_of < DAY2:
        d = st.as_of
        st = advance_state(st, entries[entries["date"] == d], races[races["date"] == d])
    assert st.as_of == DAY2
    direct = build_state(entries, races, DAY2)
    for variant in ("pre_v2", "preview"):
        a = build_today(variant, DAY2, out_dir=tmp_path / "a", load_raw=make_loader(raw_dir), state=st)
        b = build_today(variant, DAY2, out_dir=tmp_path / "b", load_raw=make_loader(raw_dir), state=direct)
        pd.testing.assert_frame_equal(a, b)


def test_state_ignores_target_day_results(long_interim):
    """状態には as_of 当日以降の結果が入らない。"""
    _, interim = long_interim
    entries, races = read_interim("entries", interim_dir=interim), read_interim("races", interim_dir=interim)
    st = build_state(entries, races, DAY2)
    limit = _dn(DAY2)
    assert (st.h_starts["dn"] < limit).all() and (st.style_starts["dn"] < limit).all()
    assert (st.races["date"] < DAY2).all()


def test_state_date_mismatch(tmp_path, long_interim):
    raw_dir, interim = long_interim
    st = build_state(read_interim("entries", interim_dir=interim), read_interim("races", interim_dir=interim), DAY1)
    with pytest.raises(ValueError):
        build_today("pre", DAY2, out_dir=tmp_path, load_raw=make_loader(raw_dir), state=st)
