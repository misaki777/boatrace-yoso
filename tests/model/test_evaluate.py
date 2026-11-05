"""評価指標・ベースライン・データ処理のテスト(lightgbm を使わない)。"""

import numpy as np
import pandas as pd
import pytest

from src.model.baselines import B0CourseRate, B1Logistic
from src.model.data import (
    clean_race_mask,
    feature_lists,
    filter_period,
    fit_temperature,
    race_softmax,
    train_row_mask,
)
from src.model.evaluate import comparison_markdown, compute_metrics, evaluate_all




def _race(race_id, finish, p, stadium=1, absent=None, valid=True):
    absent = absent or []
    return pd.DataFrame(
        {
            "race_id": race_id,
            "date": pd.Timestamp("2026-04-01"),
            "stadium_number": stadium,
            "entry_number": range(1, 7),
            "finish_position": pd.array(finish, dtype="Int8"),
            "is_win": [1 if f == 1 else 0 for f in finish],
            "is_valid_result": valid,
            "is_absent": [e in absent for e in range(1, 7)],
            "p_win": p,
        }
    )


def test_uniform_and_perfect_metrics():
    uni = [1 / 6] * 6
    df = pd.concat([_race("r1", [1, 2, 3, 4, 5, 6], uni), _race("r2", [3, 1, 2, 4, 5, 6], uni)])
    m = compute_metrics(df)
    assert m["races"] == 2
    assert m["win_logloss"] == pytest.approx(np.log(6))
    assert m["trifecta_logloss"] == pytest.approx(np.log(120))
    assert m["trifecta_races"] == 2

    sharp = [0.999995] + [0.000001] * 5
    m2 = compute_metrics(_race("r1", [1, 2, 3, 4, 5, 6], sharp))
    assert m2["win_logloss"] < 1e-4
    assert m2["top1_acc"] == 1.0
    assert m2["trifecta_top10_hit"] == 1.0


def test_exclusions():
    uni = [1 / 6] * 6
    df = pd.concat(
        [
            _race("ok", [1, 2, 3, 4, 5, 6], uni),
            _race("canceled", [None] * 6, uni, valid=False),
            _race("absent", [1, 2, 3, 4, 5, None], uni, absent=[6]),
            _race("deadheat", [1, 1, 3, 4, 5, 6], uni),
            _race("tie2", [1, 2, 2, 4, 5, 6], uni),  # 2着同着: win は使う、3連単は外す
        ]
    )
    assert clean_race_mask(df).groupby(df["race_id"]).all().to_dict() == {
        "absent": False, "canceled": False, "deadheat": False, "ok": True, "tie2": True,
    }
    m = compute_metrics(df)
    assert m["races"] == 2
    assert m["trifecta_races"] == 1
    ex = m["exclusions"]
    assert ex["invalid_result"] == 1 and ex["has_absent"] == 1 and ex["dead_heat_or_no_winner"] == 1
    assert ex["races_used"] == 2
    # 学習には欠場艇の行だけ外す(他の5艇は使う)、同着1着・中止は外す
    tm = train_row_mask(df)
    assert tm[df["race_id"] == "absent"].sum() == 5
    assert tm[df["race_id"].isin(["canceled", "deadheat"])].sum() == 0


def test_race_softmax_and_temperature():
    rid = np.repeat(["a", "b"], 6)
    s = np.tile(np.arange(6.0), 2)
    p = race_softmax(rid, s, 1.0)
    assert p[:6].sum() == pytest.approx(1) and p[6:].sum() == pytest.approx(1)
    assert np.all(np.diff(p[:6]) > 0)
    # 真の温度 2 で生成したデータから T を推定すると 2 付近になる
    rng = np.random.default_rng(0)
    n = 4000
    rid = np.repeat(np.arange(n), 6)
    scores = rng.normal(0, 2, n * 6)
    p_true = race_softmax(rid, scores, 2.0).reshape(n, 6)
    winners = np.array([rng.choice(6, p=row) for row in p_true])
    is_win = np.zeros(n * 6, dtype=int)
    is_win[np.arange(n) * 6 + winners] = 1
    t, _ = fit_temperature(rid, scores, is_win)
    assert t == pytest.approx(2.0, rel=0.15)


def test_feature_lists_no_leak():
    for v in ("pre", "preview"):
        fl = feature_lists(v)
        assert fl["features"]
        assert set(fl["categorical"]) <= set(fl["features"])
        for c in fl["features"]:
            for bad in ("rs_", "place", "odds", "payout", "technique"):
                assert bad not in c
    assert len(feature_lists("preview")["features"]) > len(feature_lists("pre")["features"])


def test_synthetic_has_spec_columns(synthetic_features):
    df = synthetic_features
    for c in ("race_id", "date", "stadium_number", "race_number", "entry_number", "racer_number",
              "finish_position", "is_win", "is_top2", "is_top3", "is_valid_result", "is_absent"):
        assert c in df.columns
    for v in ("pre", "preview"):
        assert set(feature_lists(v)["features"]) <= set(df.columns)
    assert df["race_id"].str.match(r"^\d{8}-\d{2}-\d{2}$").all()
    assert (df.groupby("race_id").size() == 6).all()


def test_baselines(synthetic_features, small_periods):
    df = synthetic_features
    train = filter_period(df, *small_periods.range_of("train"))
    valid = filter_period(df, *small_periods.range_of("valid"))
    b0 = B0CourseRate().fit(train)
    assert b0.rates.sum() == pytest.approx(1)
    assert b0.rates[0] == b0.rates.max()  # 1コースが最も強い
    b1 = B1Logistic().fit(train, valid)
    for model in (b0, b1):
        p = model.predict(valid)
        assert pd.Series(p).groupby(valid["race_id"].to_numpy()).sum().to_numpy() == pytest.approx(1)
    m0 = compute_metrics(valid.assign(p_win=b0.predict(valid)))
    m1 = compute_metrics(valid.assign(p_win=b1.predict(valid)))
    assert m0["win_logloss"] < np.log(6)
    assert m1["win_logloss"] < m0["win_logloss"]  # 全国勝率を足すと良くなる
    assert b1.params()["temperature"] > 0


def test_evaluate_all_baselines(synthetic_features, synthetic_market, small_periods):
    payouts, _ = synthetic_market
    results, preds = evaluate_all(synthetic_features, small_periods, None, payouts, n_boot=50)
    assert set(results["valid"]) == {"B0", "B1"}
    assert "test" in results
    roi = results["test"]["B1"]["roi_simulation"]
    assert [r["params"]["k"] for r in roi] == [1, 3, 5, 10]
    assert all(r["roi_ci95"] is not None for r in roi)
    md = comparison_markdown({k: v for k, v in results.items() if not k.startswith("_")})
    assert "| B1 |" in md and "## test" in md


def test_harville_gamma_meta():
    # lightgbm を import しない関数なので、libgomp が無くても動く
    from src.model.lgbm import gammas_from_meta, harville_gamma_meta

    assert harville_gamma_meta(1.0, 1.0) is None
    assert harville_gamma_meta(0.85, 0.75) == {"gamma2": 0.85, "gamma3": 0.75}
    assert gammas_from_meta({"harville_gamma": None}) == (1.0, 1.0)
    assert gammas_from_meta({"harville_gamma": {"gamma2": 0.85, "gamma3": 0.75}, "gamma2": 1.0}) == (0.85, 0.75)
    assert gammas_from_meta({"gamma2": 0.9, "gamma3": 0.8}) == (0.9, 0.8)  # 古い meta
    assert gammas_from_meta({}) == (1.0, 1.0)
