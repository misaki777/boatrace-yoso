"""展開予想の特徴量(F-9)のテスト。"""

from __future__ import annotations

import pandas as pd
import pytest

from features_testlib import DAY1, DAY2, load_fixture, write_raw
from src.features import feature_sets as fs
from src.features.build_features import build_training as build_boats
from src.features.build_interim import build_interim, read_interim
from src.features.build_tenkai import build_today, build_training
from src.features.history_state import build_state
from src.features.raw_source import make_loader


def test_feature_list():
    for v in fs.TENKAI_VARIANTS:
        feats = fs.tenkai_features_for(v)
        assert len(feats) == len(set(feats))
        assert set(fs.tenkai_categorical_features(v)) <= set(feats)
        for c in feats:
            for bad in fs.FORBIDDEN_SUBSTRINGS:
                assert bad not in c
    assert set(fs.tenkai_features_for("pre")) < set(fs.tenkai_features_for("preview"))


@pytest.mark.parametrize("variant", fs.TENKAI_VARIANTS)
def test_one_row_per_race(tmp_path, interim_dir, variant):
    df = build_training(variant, DAY1, DAY2, interim_dir=interim_dir, out_dir=tmp_path)
    assert (tmp_path / f"tenkai_{variant}.parquet").exists()
    assert df["race_id"].is_unique and len(df) == 8
    assert list(df.columns) == fs.TENKAI_KEY_COLUMNS + fs.TENKAI_TARGET_COLUMNS + [
        c for c in fs.tenkai_features_for(variant) if c not in fs.TENKAI_KEY_COLUMNS]
    r1 = df.set_index("race_id").loc["20261101-02-01"]
    assert r1["technique_number"] == 2  # 差し
    # 5艇のレースは c6 が空
    r = df.set_index("race_id").loc["20261102-02-02"]
    assert pd.isna(r["c6_national_win_rate"]) and r["c5_entry_number"] == 5
    # 中止のレースは決まり手が null
    assert pd.isna(df.set_index("race_id").loc["20261101-02-06", "technique_number"])


def test_values_match_boat_features(tmp_path, interim_dir):
    """c{k}_ の値は、予定コース k の艇の v2 特徴量と同じ。"""
    t = build_training("pre", DAY1, DAY2, interim_dir=interim_dir, out_dir=tmp_path / "t").set_index("race_id")
    b = build_boats("pre_v2", DAY1, DAY2, interim_dir=interim_dir, out_dir=tmp_path / "b")
    race = b[b["race_id"] == "20261102-02-01"].set_index("entry_number")
    for k in range(1, 7):
        for name in ("national_win_rate", "h_cs_nige_all", "h_cs_sashi_all", "r_sty_st_diff_inner"):
            assert t.loc["20261102-02-01", f"c{k}_{name}"] == pytest.approx(race.loc[k, name], nan_ok=True)
    assert t.loc["20261102-02-01", "r_sty_in_nige"] == pytest.approx(race.loc[1, "r_sty_in_nige"])


def test_preview_reorders_by_exhibition_course(tmp_path, raw_dir):
    raw = load_fixture("openapi", DAY2)
    pv = raw["programs"]["stadiums"]["2"]["races"]["1"]["preview"]["racers"]
    pv["1"]["course_number"], pv["2"]["course_number"] = 2, 1   # 2号艇が1コースへ(前付け)
    write_raw(raw_dir, "openapi", DAY2, raw)
    out = tmp_path / "i"
    build_interim(DAY1, DAY2, load_raw=make_loader(raw_dir), out_dir=out, verbose=False)
    pre = build_training("pre", DAY1, DAY2, interim_dir=out, out_dir=tmp_path / "a").set_index("race_id")
    pv_ = build_training("preview", DAY1, DAY2, interim_dir=out, out_dir=tmp_path / "b").set_index("race_id")
    rid = "20261102-02-01"
    assert pre.loc[rid, "c1_entry_number"] == 1 and pv_.loc[rid, "c1_entry_number"] == 2
    assert pv_.loc[rid, "c2_entry_number"] == 1
    assert pv_.loc[rid, "c1_national_win_rate"] == pytest.approx(pre.loc[rid, "c2_national_win_rate"])


def _shuffle(raw):
    for st in raw["programs"]["stadiums"].values():
        for race in st["races"].values():
            rs = race["result"]["racers"]
            keys = sorted(rs)
            vals = [rs[k]["place_number"] for k in keys][::-1]
            for k, p in zip(keys, vals):
                rs[k]["place_number"] = p
                rs[k]["start_timing"] = 0.3
            if race["result"].get("technique_number") is not None:
                race["result"]["technique_number"] = 4
    return raw


@pytest.mark.parametrize("variant", fs.TENKAI_VARIANTS)
def test_leak_same_day_results(tmp_path, raw_dir, interim_dir, variant):
    """D日の結果(着順・ST・決まり手)を変えても、D日の特徴量は変わらない(目的変数だけ変わる)。"""
    a = build_training(variant, DAY1, DAY2, interim_dir=interim_dir, out_dir=tmp_path / "a")
    write_raw(raw_dir, "openapi", DAY2, _shuffle(load_fixture("openapi", DAY2)))
    out = tmp_path / "i2"
    build_interim(DAY1, DAY2, load_raw=make_loader(raw_dir), out_dir=out, verbose=False)
    b = build_training(variant, DAY1, DAY2, interim_dir=out, out_dir=tmp_path / "b")
    feats = fs.tenkai_features_for(variant)
    a2 = a[a["date"] == DAY2].reset_index(drop=True)
    b2 = b[b["date"] == DAY2].reset_index(drop=True)
    pd.testing.assert_frame_equal(a2[feats], b2[feats])
    assert not a2["technique_number"].equals(b2["technique_number"])


@pytest.mark.parametrize("variant", fs.TENKAI_VARIANTS)
def test_today_matches_training_and_state(tmp_path, raw_dir, interim_dir, variant):
    full = build_training(variant, DAY1, DAY2, interim_dir=interim_dir, out_dir=tmp_path / "f")
    today = build_today(variant, DAY2, interim_dir=interim_dir, out_dir=tmp_path / "t",
                        load_raw=make_loader(raw_dir))
    assert (tmp_path / "t" / "today" / f"20261102_tenkai_{variant}.parquet").exists()
    st = build_state(read_interim("entries", interim_dir=interim_dir),
                     read_interim("races", interim_dir=interim_dir), DAY2)
    via_state = build_today(variant, DAY2, out_dir=tmp_path / "s", load_raw=make_loader(raw_dir), state=st)
    feats = fs.tenkai_features_for(variant)
    a = full[full["date"] == DAY2].reset_index(drop=True)
    pd.testing.assert_frame_equal(a[feats], today[feats])
    pd.testing.assert_frame_equal(a[feats], via_state[feats])
    assert today["technique_number"].isna().all()
