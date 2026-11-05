"""レース運びの特徴量(v2)のテスト。値の検算、縮小、variant の切替、リーク防止。"""

from __future__ import annotations

import pandas as pd
import pytest

from features_testlib import DAY1, DAY2, load_fixture, write_raw
from src.features import feature_sets as fs
from src.features.build_features import build_today, build_training
from src.features.build_interim import build_interim, read_interim
from src.features.raw_source import make_loader
from src.features.style import STYLE_ALPHA, STYLE_R_FEATURES, style_h_feature_names


def _row(df, race_id, entry):
    return df.set_index(["race_id", "entry_number"]).loc[(race_id, entry)]


@pytest.fixture
def f_v2(tmp_path, interim_dir):
    return build_training("pre_v2", DAY1, DAY2, interim_dir=interim_dir, out_dir=tmp_path / "f")


def test_feature_sets_v2_contract():
    assert fs.STYLE_H_FEATURES == style_h_feature_names()
    assert fs.STYLE_R_FEATURES == STYLE_R_FEATURES
    # 既存の variant は変わらない(追加のみ)
    assert fs.features_for("pre_v2")[: len(fs.PRE_FEATURES)] == fs.PRE_FEATURES
    assert fs.features_for("preview_v2")[: len(fs.PREVIEW_FEATURES)] == fs.PREVIEW_FEATURES
    assert not set(fs.PRE_FEATURES) & set(fs.STYLE_H_FEATURES + fs.STYLE_R_FEATURES)
    assert len(set(fs.PREVIEW_V2_FEATURES)) == len(fs.PREVIEW_V2_FEATURES)
    assert fs.categorical_features("pre_v2") == fs.categorical_features("pre")


def test_existing_variant_output_unchanged(tmp_path, interim_dir, f_v2):
    pre = build_training("pre", DAY1, DAY2, interim_dir=interim_dir, out_dir=tmp_path / "p")
    assert list(pre.columns) == fs.KEY_COLUMNS + fs.TARGET_COLUMNS + fs.META_COLUMNS + [
        c for c in fs.PRE_FEATURES if c not in fs.KEY_COLUMNS]
    pd.testing.assert_frame_equal(pre[fs.PRE_FEATURES], f_v2[fs.PRE_FEATURES])


def test_style_values_with_shrinkage(interim_dir, f_v2):
    # 5242 は1日目の1R(2着)と5R(1着・決まり手は1Rのコピーで「差し」)に1コースで出た
    e = read_interim("entries", interim_dir=interim_dir)
    r = read_interim("races", interim_dir=interim_dir)
    d1 = e[(e["date"] == DAY1) & e["place_number"].notna()].merge(r[["race_id", "technique_number"]], on="race_id")
    c1 = d1[d1["rs_course_number"] == 1]
    p_sashi = ((c1["finish_position"] == 1) & (c1["technique_number"] == 2)).sum() / len(c1)
    p_second = (c1["finish_position"] == 2).sum() / len(c1)
    row = _row(f_v2, "20261102-02-01", 1)
    a = STYLE_ALPHA
    assert row["h_cs_n_all"] == 2 and row["h_cs_n_180d"] == 2
    assert row["h_cs_sashi_all"] == pytest.approx((1 + a * p_sashi) / (2 + a), abs=1e-5)
    assert row["h_cs_second_all"] == pytest.approx((1 + a * p_second) / (2 + a), abs=1e-5)
    # 過去が無い1日目は件数0・率は不明
    d1f = f_v2[f_v2["date"] == DAY1]
    assert (d1f["h_cs_n_all"] == 0).all() and d1f["h_cs_nige_all"].isna().all()


def test_style_r_features(f_v2):
    race = f_v2[f_v2["race_id"] == "20261102-02-01"].set_index("entry_number")
    # 1コース(1号艇)の逃げ率がレース全体に配られる
    assert (race["r_sty_in_nige"] == race.loc[1, "h_cs_nige_all"]).all()
    assert race.loc[1, "r_sty_nige_minus_sashi2"] == pytest.approx(
        race.loc[1, "h_cs_nige_all"] - race.loc[2, "h_cs_sashi_all"], abs=1e-6)
    att = (race.loc[[3, 4], "h_cs_makuri_all"] + race.loc[[3, 4], "h_cs_makurisashi_all"]).max()
    assert race.loc[5, "r_sty_nige_minus_makuri34"] == pytest.approx(race.loc[1, "h_cs_nige_all"] - att, abs=1e-6)
    assert race.loc[1, "r_sty_st_diff_inner"] == 0.0
    inner = race.loc[[1, 2], "h_cs_st_mean_all"].mean()
    assert race.loc[3, "r_sty_st_diff_inner"] == pytest.approx(race.loc[3, "h_cs_st_mean_all"] - inner, abs=1e-5)


def test_preview_v2_uses_exhibition_course(tmp_path, raw_dir):
    # スタート展示で 1号艇と2号艇の進入が入れ替わったら、予定コースも入れ替わる
    raw = load_fixture("openapi", DAY2)
    pv = raw["programs"]["stadiums"]["2"]["races"]["1"]["preview"]["racers"]
    pv["1"]["course_number"], pv["2"]["course_number"] = 2, 1
    write_raw(raw_dir, "openapi", DAY2, raw)
    out = tmp_path / "i"
    build_interim(DAY1, DAY2, load_raw=make_loader(raw_dir), out_dir=out, verbose=False)
    pre = build_training("pre_v2", DAY1, DAY2, interim_dir=out, out_dir=tmp_path / "a")
    pvw = build_training("preview_v2", DAY1, DAY2, interim_dir=out, out_dir=tmp_path / "b")
    a, b = _row(pre, "20261102-02-01", 1), _row(pvw, "20261102-02-01", 1)
    assert a["h_cs_n_all"] == 2 and b["h_cs_n_all"] == 0  # 2コースでの過去はない
    assert set(fs.PREVIEW_V2_FEATURES) <= set(pvw.columns)


def _shuffle_results(raw):
    for st in raw["programs"]["stadiums"].values():
        for race in st["races"].values():
            res = race["result"]
            rs = res["racers"]
            keys = sorted(rs)
            vals = [(rs[k]["place_number"], rs[k]["start_timing"]) for k in keys][::-1]
            for k, (p, s) in zip(keys, vals):
                rs[k]["place_number"], rs[k]["start_timing"] = p, s
                if rs[k]["course_number"] is not None:
                    rs[k]["course_number"] = 7 - int(k)
            if res.get("technique_number") is not None:
                res["technique_number"] = 3
    return raw


@pytest.mark.parametrize("variant", ["pre_v2", "preview_v2"])
def test_leak_same_day_results(tmp_path, raw_dir, interim_dir, variant):
    """D日の結果(着順・ST・進入・決まり手)を変えても、D日の特徴量は変わらない。"""
    base = build_training(variant, DAY1, DAY2, interim_dir=interim_dir, out_dir=tmp_path / "a")
    write_raw(raw_dir, "openapi", DAY2, _shuffle_results(load_fixture("openapi", DAY2)))
    out = tmp_path / "i2"
    build_interim(DAY1, DAY2, load_raw=make_loader(raw_dir), out_dir=out, verbose=False)
    changed = build_training(variant, DAY1, DAY2, interim_dir=out, out_dir=tmp_path / "b")
    feats = fs.features_for(variant)
    a = base[base["date"] == DAY2].reset_index(drop=True)
    b = changed[changed["date"] == DAY2].reset_index(drop=True)
    pd.testing.assert_frame_equal(a[feats], b[feats])


def test_leak_previous_day_changes_style(tmp_path, raw_dir, interim_dir):
    base = build_training("pre_v2", DAY1, DAY2, interim_dir=interim_dir, out_dir=tmp_path / "a")
    write_raw(raw_dir, "turnmark", DAY1, _shuffle_results(load_fixture("turnmark", DAY1)))
    out = tmp_path / "i2"
    build_interim(DAY1, DAY2, load_raw=make_loader(raw_dir), out_dir=out, verbose=False)
    changed = build_training("pre_v2", DAY1, DAY2, interim_dir=out, out_dir=tmp_path / "b")
    a = base[base["date"] == DAY2].reset_index(drop=True)
    b = changed[changed["date"] == DAY2].reset_index(drop=True)
    assert not a["h_cs_n_all"].equals(b["h_cs_n_all"])


def test_today_v2_matches_training(tmp_path, raw_dir, interim_dir, f_v2):
    today = build_today("pre_v2", DAY2, interim_dir=interim_dir, out_dir=tmp_path / "t",
                        load_raw=make_loader(raw_dir))
    feats = fs.features_for("pre_v2")
    a = f_v2[f_v2["date"] == DAY2].reset_index(drop=True)
    pd.testing.assert_frame_equal(a[feats], today[feats])
