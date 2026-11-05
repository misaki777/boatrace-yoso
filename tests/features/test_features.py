"""特徴量(P・R・H)、feature_sets、リーク防止、当日用のテスト。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from features_testlib import DAY1, DAY2, load_fixture, write_raw
from src.features import feature_sets as fs
from src.features.build_features import (
    build_today, build_training, classify_race_kind, make_feature_frame,
)
from src.features.build_interim import build_interim, read_interim
from src.features.raw_source import make_loader


def _row(df: pd.DataFrame, race_id: str, entry: int) -> pd.Series:
    return df.set_index(["race_id", "entry_number"]).loc[(race_id, entry)]


@pytest.fixture
def features(tmp_path, interim_dir) -> pd.DataFrame:
    return build_training("pre", DAY1, DAY2, interim_dir=interim_dir, out_dir=tmp_path / "features")


# ---------------------------------------------------------------------------
# feature_sets(契約)
# ---------------------------------------------------------------------------


def test_feature_sets_consistency():
    assert len(set(fs.PRE_FEATURES)) == len(fs.PRE_FEATURES)
    assert len(set(fs.PREVIEW_FEATURES)) == len(fs.PREVIEW_FEATURES)
    assert set(fs.PRE_FEATURES) <= set(fs.PREVIEW_FEATURES)
    assert set(fs.CATEGORICAL_FEATURES) <= set(fs.PREVIEW_FEATURES)
    assert set(fs.categorical_features("pre")) == {"stadium_number", "grade_number", "race_kind"}
    # pre には直前情報(pv_)が入らない
    assert not [c for c in fs.PRE_FEATURES if "pv_" in c]
    # 特徴量と目的変数・メタが重ならない
    assert not set(fs.PREVIEW_FEATURES) & set(fs.TARGET_COLUMNS + fs.META_COLUMNS)


@pytest.mark.parametrize("variant", fs.VARIANTS)
def test_no_forbidden_columns(variant):
    # リーク防止: 本番の結果・払戻・オッズ・決まり手の列を特徴量に入れない
    for col in fs.features_for(variant):
        for bad in fs.FORBIDDEN_SUBSTRINGS:
            assert bad not in col, f"{col} に {bad} が含まれる"


def test_output_columns(features):
    feats = fs.features_for("pre")
    assert list(features.columns[: len(fs.KEY_COLUMNS)]) == fs.KEY_COLUMNS
    assert set(feats) <= set(features.columns)
    for c in feats:
        assert pd.api.types.is_numeric_dtype(features[c]), c
        assert not pd.api.types.is_extension_array_dtype(features[c]), c  # LightGBM に渡せる型
    # 出力に結果系の列が特徴量として紛れ込んでいない
    extra = set(features.columns) - set(feats) - set(fs.KEY_COLUMNS + fs.TARGET_COLUMNS + fs.META_COLUMNS)
    assert not extra
    assert len(features) == 47


# ---------------------------------------------------------------------------
# P・R
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("subtitle,kind", [
    ("予選", "yosen"), ("準優勝戦", "jun_yusho"), ("優勝戦", "yusho"), ("予選特選", "tokusen"),
    ("記者選抜戦", "tokusen"), ("ドリーム", "tokusen"), ("一般", "ippan"), ("朝トク一般", "ippan"),
    ("かけうどん6", "other"), (None, "other"),
])
def test_race_kind(subtitle, kind):
    assert classify_race_kind(subtitle) == kind


def test_p_features(features):
    r = _row(features, "20261102-02-01", 1)
    assert r["race_kind"] == fs.RACE_KIND_CODES["yosen"]
    # 支部(埼玉=11)と戸田(埼玉)が一致
    assert r["is_home_branch"] == 1.0
    assert _row(features, "20261102-02-01", 2)["is_home_branch"] == 0.0  # 福井
    assert _row(features, "20261102-02-12", 1)["race_kind"] == fs.RACE_KIND_CODES["tokusen"]


def test_r_features(features):
    race = features[features["race_id"] == "20261102-02-01"].set_index("entry_number")
    # 全国勝率: 3.59, 5.71, 3.90, 4.06, 3.91, 4.67
    assert list(race["r_win_rate_rank"]) == [6, 1, 5, 3, 4, 2]
    assert race.loc[2, "r_win_rate_diff_max"] == pytest.approx(5.71 - 4.67, abs=1e-4)
    assert race.loc[1, "r_win_rate_diff_max"] == pytest.approx(3.59 - 5.71, abs=1e-4)
    mean = np.mean([3.59, 5.71, 3.90, 4.06, 3.91, 4.67])
    assert race.loc[3, "r_win_rate_diff_mean"] == pytest.approx(3.90 - mean, abs=1e-4)
    assert race.loc[1, "r_inner_strength"] == 0.0
    assert race.loc[4, "r_inner_strength"] == pytest.approx(5.71, abs=1e-4)
    assert (race["r_a1_count"] == race["r_a1_count"].iloc[0]).all()
    # 5艇のレースでは順位は1〜5
    r5 = features[features["race_id"] == "20261102-02-02"]
    assert r5["r_win_rate_rank"].max() == 5


# ---------------------------------------------------------------------------
# H
# ---------------------------------------------------------------------------


def test_h_features_first_day_is_empty(features):
    d1 = features[features["date"] == DAY1]
    assert (d1["h_racer_n_90d"] == 0).all()
    assert d1["h_racer_win_rate_90d"].isna().all()
    assert d1["h_days_since_last_race"].isna().all()


def test_h_features_values(features):
    # 2日目の 2場1R は、1日目の 1R と 5R(同着)と同じ選手。6R は中止なので数えない
    r1 = _row(features, "20261102-02-01", 1)
    assert r1["h_racer_n_90d"] == 2
    assert r1["h_racer_win_rate_90d"] == pytest.approx(0.5)   # 2着, 1着(同着)
    assert r1["h_racer_top2_90d"] == pytest.approx(1.0)
    assert r1["h_racer_avg_finish_90d"] == pytest.approx(1.5)
    assert r1["h_days_since_last_race"] == 1
    assert _row(features, "20261102-02-01", 2)["h_racer_win_rate_90d"] == pytest.approx(1.0)
    assert _row(features, "20261102-02-01", 3)["h_racer_avg_finish_90d"] == pytest.approx(4.0)
    # 同じ節(同じ場・同じタイトル・連続した日)のモーター2連対率と平均着順
    assert r1["h_motor_series_top2"] == pytest.approx(1.0)
    assert _row(features, "20261102-02-01", 3)["h_motor_series_top2"] == pytest.approx(0.0)
    assert r1["h_racer_series_avg_finish"] == pytest.approx(1.5)
    # コース別1着率(平滑化 m=10)。1コースの全体: 3走1勝。この選手: 2走1勝
    assert r1["h_racer_course_win_rate"] == pytest.approx((1 + 10 * (1 / 3)) / (2 + 10), abs=1e-5)
    # 場・コース別(365日): 戸田1コース 3走1勝
    assert r1["h_stadium_course_win_rate_365d"] == pytest.approx(1 / 3, abs=1e-5)
    # 失格は7着として平均着順に入る(1日目4Rの3号艇 → 2日目には出ていないので、別の選手で確認)
    # 4934 は 1日目4R(2号艇・5着)→ 2日目12R(6号艇)
    r = _row(features, "20261102-02-12", 6)
    assert r["h_racer_n_90d"] == 1 and r["h_racer_avg_finish_90d"] == pytest.approx(5.0)
    # 別の場(江戸川)は節がつながらない
    assert features.loc[features["race_id"] == "20261102-03-02", "h_motor_series_top2"].isna().all()


def test_disqualified_counts_as_seven(tmp_path, raw_dir):
    # 1日目4Rの失格選手(4875)を2日目にも出して、平均着順が7になることを確認する
    raw = load_fixture("openapi", DAY2)
    race = raw["programs"]["stadiums"]["2"]["races"]["1"]
    race["racers"]["1"]["number"] = 4875
    write_raw(raw_dir, "openapi", DAY2, raw)
    out = tmp_path / "interim2"
    build_interim(DAY1, DAY2, load_raw=make_loader(raw_dir), out_dir=out, verbose=False)
    f = build_training("pre", DAY1, DAY2, interim_dir=out, out_dir=tmp_path / "f2")
    r = _row(f, "20261102-02-01", 1)
    assert r["h_racer_n_90d"] == 1 and r["h_racer_avg_finish_90d"] == pytest.approx(7.0)
    assert r["h_racer_win_rate_90d"] == 0.0


# ---------------------------------------------------------------------------
# リーク防止(02_features.md 3章・5章)
# ---------------------------------------------------------------------------


def _shuffle_results(raw: dict) -> dict:
    """結果(着順・ST・進入)を入れ替える。出走表と直前情報はそのまま。"""
    for st in raw["programs"]["stadiums"].values():
        for race in st["races"].values():
            rs = race["result"]["racers"]
            keys = sorted(rs)
            places = [rs[k]["place_number"] for k in keys]
            sts = [rs[k]["start_timing"] for k in keys]
            for k, p, s in zip(keys, places[::-1], sts[::-1]):
                rs[k]["place_number"] = p
                rs[k]["start_timing"] = s
                rs[k]["course_number"] = 7 - int(k) if rs[k]["course_number"] is not None else None
    return raw


def _features_for(tmp_path, raw_dir, name):
    out = tmp_path / f"interim_{name}"
    build_interim(DAY1, DAY2, load_raw=make_loader(raw_dir), out_dir=out, verbose=False)
    return build_training("pre", DAY1, DAY2, interim_dir=out, out_dir=tmp_path / f"f_{name}")


def test_leak_same_day_results_do_not_change_features(tmp_path, raw_dir, features):
    """D日の結果を変えても、D日の特徴量(P・R・H すべて)は変わらない。"""
    write_raw(raw_dir, "openapi", DAY2, _shuffle_results(load_fixture("openapi", DAY2)))
    changed = _features_for(tmp_path, raw_dir, "shuffled")
    feats = fs.features_for("pre")
    a = features[features["date"] == DAY2].reset_index(drop=True)
    b = changed[changed["date"] == DAY2].reset_index(drop=True)
    pd.testing.assert_frame_equal(a[feats], b[feats])
    # 目的変数のほうは変わっている(テストが空振りしていないことの確認)
    assert not a["finish_position"].equals(b["finish_position"])


def test_leak_previous_day_results_do_change_features(tmp_path, raw_dir, features):
    """逆に D−1 日の結果を変えると、D 日の H 特徴量が変わる(テストの感度の確認)。"""
    write_raw(raw_dir, "turnmark", DAY1, _shuffle_results(load_fixture("turnmark", DAY1)))
    changed = _features_for(tmp_path, raw_dir, "prev")
    a = features[features["date"] == DAY2].reset_index(drop=True)
    b = changed[changed["date"] == DAY2].reset_index(drop=True)
    assert not a["h_racer_win_rate_90d"].equals(b["h_racer_win_rate_90d"])


def test_leak_future_days_do_not_change_features(tmp_path, raw_dir, features):
    """D+1 日のデータがあってもなくても、D 日の特徴量は同じ。"""
    out = tmp_path / "interim_day1"
    build_interim(DAY1, DAY1, load_raw=make_loader(raw_dir), out_dir=out, verbose=False)
    # 同じ月なので月ファイルには2日目も入る。読み込み側で2日目を除いて作る
    entries = read_interim("entries", None, DAY1, out)
    races = read_interim("races", None, DAY1, out)
    only1 = make_feature_frame(entries, races, entries, races, "pre")
    feats = fs.features_for("pre")
    a = features[features["date"] == DAY1].reset_index(drop=True)
    pd.testing.assert_frame_equal(a[feats], only1[feats])


# ---------------------------------------------------------------------------
# 当日用(--date)
# ---------------------------------------------------------------------------


def test_today_matches_training(tmp_path, raw_dir, interim_dir, features):
    """当日用は学習用と同じ関数を通り、特徴量が一致する。目的変数は null。"""
    today = build_today("pre", DAY2, interim_dir=interim_dir, out_dir=tmp_path / "ft",
                        load_raw=make_loader(raw_dir))
    assert (tmp_path / "ft" / "today" / "20261102_pre.parquet").exists()
    assert list(today.columns) == list(features.columns)
    for c in fs.TARGET_COLUMNS + fs.META_COLUMNS:
        assert today[c].isna().all(), c
    feats = fs.features_for("pre")
    a = features[features["date"] == DAY2].reset_index(drop=True)
    pd.testing.assert_frame_equal(a[feats], today[feats])


def test_today_without_results(tmp_path, raw_dir, interim_dir):
    """締切前(結果なし)の出走表でも作れる。"""
    raw = load_fixture("openapi", DAY2)
    for st in raw["programs"]["stadiums"].values():
        for race in st["races"].values():
            race["result"] = None
            race["preview"] = None
    write_raw(raw_dir, "openapi", DAY2, raw)
    today = build_today("pre", DAY2, interim_dir=interim_dir, out_dir=tmp_path / "ft",
                        load_raw=make_loader(raw_dir))
    assert len(today) == 23
    assert today["h_racer_n_90d"].notna().all()


def test_preview_variant_builds(tmp_path, interim_dir):
    f = build_training("preview", DAY1, DAY2, interim_dir=interim_dir, out_dir=tmp_path / "fp")
    assert set(fs.PREVIEW_FEATURES) <= set(f.columns)
    r = _row(f, "20261102-02-01", 1)
    assert r["r_pv_exhibition_rank"] >= 1
    assert r["pv_wind_class"] == fs.WIND_CLASS_CODES["none"]
