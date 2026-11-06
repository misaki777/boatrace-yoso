"""展示の相対値(preview_v3)のテスト。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from features_testlib import DAY1, DAY2, load_fixture, write_raw
from src.features import feature_sets as fs
from src.features.build_features import build_today, build_training
from src.features.build_interim import build_interim, read_interim
from src.features.build_tenkai import build_today as tenkai_today, build_training as tenkai_training
from src.features.exhibition import EX_FEATURES, compute_ex_features
from src.features.history_state import build_state
from src.features.raw_source import make_loader


def test_contract():
    assert fs.EX_FEATURES == EX_FEATURES
    assert fs.features_for("preview_v3") == fs.PREVIEW_V2_FEATURES + EX_FEATURES
    assert fs.features_for("preview_v2") == fs.PREVIEW_V2_FEATURES  # 既存は変わらない
    assert fs.tenkai_features_for("preview")[:] == [c for c in fs.tenkai_features_for("preview")]
    assert set(fs.tenkai_features_for("preview")) < set(fs.tenkai_features_for("preview_v3"))
    for c in EX_FEATURES:
        assert not any(b in c for b in fs.FORBIDDEN_SUBSTRINGS)


def test_values_handmade():
    # 枠番1〜6。展示の進入は 1,2,3,4,5,6(5号艇と6号艇は入れ替わり → 予定コース 6, 5)
    df = pd.DataFrame({
        "race_id": ["r"] * 6, "entry_number": [1, 2, 3, 4, 5, 6],
        "pv_start_timing": [0.10, 0.05, 0.12, -0.02, 0.08, 0.15],   # 4号艇は F.02
        "pv_exhibition_time": [6.80, 6.75, 6.70, 6.72, 6.69, 6.85],
    })
    planned = pd.Series([1, 2, 3, 4, 6, 5], dtype="float64")
    f = compute_ex_features(df, planned).set_index(df["entry_number"])
    # F は最速扱い
    assert list(f["r_pv_st_rank"]) == [4, 2, 5, 1, 3, 6]
    assert list(f["pv_st_flying"]) == [0, 0, 0, 1, 0, 0]
    # グループ内順位: 内側(1,2,3号艇)と外側(4,5,6号艇)
    assert list(f["r_pv_st_rank_group"]) == [2, 1, 3, 1, 2, 3]
    # 差は F を 0.00 として
    assert f.loc[4, "r_pv_st_diff_best"] == pytest.approx(0.0)
    assert f.loc[1, "r_pv_st_diff_best"] == pytest.approx(0.10)
    # すぐ内側: 5号艇(予定6コース)の内側は予定5コースの6号艇
    assert f.loc[5, "r_pv_st_diff_inner"] == pytest.approx(0.08 - 0.15)
    assert f.loc[4, "r_pv_st_diff_inner"] == pytest.approx(0.0 - 0.12)
    assert np.isnan(f.loc[1, "r_pv_st_diff_inner"])
    assert list(f["r_pv_exhibition_rank_group"]) == [3, 2, 1, 2, 1, 3]
    # 外側で展示ST・展示タイムとも1位の艇はいない(ST は4号艇、タイムは5号艇)
    assert list(f["pv_outer_both_top"]) == [0, 0, 0, 0, 0, 0]
    df.loc[3, "pv_exhibition_time"] = 6.60
    f2 = compute_ex_features(df, planned).set_index(df["entry_number"])
    assert f2.loc[4, "pv_outer_both_top"] == 1
    # 直前情報が無い艇は NaN(内側の艇も)。値がある艇は 0/1
    df.loc[[0, 5], "pv_start_timing"] = np.nan
    df.loc[2, "pv_exhibition_time"] = np.nan
    f3 = compute_ex_features(df, planned).set_index(df["entry_number"])
    assert f3.loc[[1, 3, 6], "pv_outer_both_top"].isna().all()
    assert f3.loc[[2, 4, 5], "pv_outer_both_top"].notna().all()


def test_tenkai_v3_drops_always_missing_column():
    feats = fs.tenkai_features_for("preview_v3")
    assert "c1_r_pv_st_diff_inner" not in feats and "c2_r_pv_st_diff_inner" in feats
    assert fs.tenkai_features_for("preview") == [c for c in fs.tenkai_features_for("preview")]


def _shuffle(raw):
    for st in raw["programs"]["stadiums"].values():
        for race in st["races"].values():
            rs = race["result"]["racers"]
            keys = sorted(rs)
            vals = [rs[k]["place_number"] for k in keys][::-1]
            for k, p in zip(keys, vals):
                rs[k]["place_number"], rs[k]["start_timing"] = p, 0.3
    return raw


def test_leak_and_consistency(tmp_path, raw_dir, interim_dir):
    v = "preview_v3"
    feats = fs.features_for(v)
    full = build_training(v, DAY1, DAY2, interim_dir=interim_dir, out_dir=tmp_path / "f")
    # preview_v2 の列の値は変わらない
    v2 = build_training("preview_v2", DAY1, DAY2, interim_dir=interim_dir, out_dir=tmp_path / "v2")
    pd.testing.assert_frame_equal(full[fs.PREVIEW_V2_FEATURES], v2[fs.PREVIEW_V2_FEATURES])
    d2 = full[full["date"] == DAY2].reset_index(drop=True)
    assert d2["r_pv_st_rank"].notna().any()
    # 当日用・状態から
    today = build_today(v, DAY2, interim_dir=interim_dir, out_dir=tmp_path / "t", load_raw=make_loader(raw_dir))
    st = build_state(read_interim("entries", interim_dir=interim_dir), read_interim("races", interim_dir=interim_dir), DAY2)
    via = build_today(v, DAY2, out_dir=tmp_path / "s", load_raw=make_loader(raw_dir), state=st)
    pd.testing.assert_frame_equal(d2[feats], today[feats])
    pd.testing.assert_frame_equal(d2[feats], via[feats])
    # 展開予想 preview_v3
    tf = tenkai_training("preview_v3", DAY1, DAY2, interim_dir=interim_dir, out_dir=tmp_path / "tk")
    tt = tenkai_today("preview_v3", DAY2, out_dir=tmp_path / "tk2", load_raw=make_loader(raw_dir), state=st)
    tfe = fs.tenkai_features_for("preview_v3")
    pd.testing.assert_frame_equal(tf[tf["date"] == DAY2].reset_index(drop=True)[tfe], tt[tfe])
    # リーク: D日の結果を変えても変わらない
    write_raw(raw_dir, "openapi", DAY2, _shuffle(load_fixture("openapi", DAY2)))
    out = tmp_path / "i2"
    build_interim(DAY1, DAY2, load_raw=make_loader(raw_dir), out_dir=out, verbose=False)
    ch = build_training(v, DAY1, DAY2, interim_dir=out, out_dir=tmp_path / "c")
    pd.testing.assert_frame_equal(d2[feats], ch[ch["date"] == DAY2].reset_index(drop=True)[feats])
