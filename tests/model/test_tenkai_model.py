"""M-14 展開予想のテスト(較正・基準は lightgbm 不要、学習と予想は lightgbm がある環境だけ)。"""

import numpy as np
import pandas as pd
import pytest

from src.model import tenkai as tk
from src.model.lgbm import lightgbm_available


def _frame(n=600, seed=0):
    """c1_〜c6_ の列を持つ合成の展開テーブル。1コースの強さで逃げが決まる。"""
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2026-04-01", "2026-10-31")
    d = rng.choice(dates, n)
    strength = rng.normal(0, 1, n)
    logits = np.column_stack([1.5 + 1.5 * strength, 0.0 - strength, 0.2 - strength, 0.0 * strength, -0.7 + 0 * strength, -3 + 0 * strength])
    P = tk.softmax(logits)
    y = np.array([rng.choice(6, p=p) for p in P])
    df = pd.DataFrame({"race_id": [f"{pd.Timestamp(x):%Y%m%d}-{i % 24 + 1:02d}-{i % 12 + 1:02d}" for i, x in enumerate(d)],
                       "date": d, "stadium_number": (np.arange(n) % 24 + 1).astype("int8"),
                       "technique_number": pd.array(y + 1, dtype="Int8"), "is_valid_result": True})
    df["y"] = y
    return df, P


def test_baselines_and_metrics():
    df, P = _frame()
    prior = tk.baseline_overall(df)
    assert prior.sum() == pytest.approx(1)
    b1 = tk.baseline_stadium(df, df.iloc[:50], prior)
    assert np.allclose(b1.sum(axis=1), 1)
    m_true = tk.metrics(P, df["y"].to_numpy())
    m_b0 = tk.metrics(np.tile(prior, (len(df), 1)), df["y"].to_numpy())
    assert m_true["logloss"] < m_b0["logloss"]
    assert set(m_true["calibration"]) == set(tk.CLASSES)


def test_stadium_baseline_uses_only_past():
    df, _ = _frame()
    target = df.iloc[:20].copy()
    prior = tk.baseline_overall(df)
    b1 = tk.baseline_stadium(df, target, prior)
    later = df[df["date"] >= df["date"].max()].copy()
    later["y"] = 5  # 対象日以降の結果を変えても基準は変わらない
    df2 = pd.concat([df[df["date"] < df["date"].max()], later])
    b1b = tk.baseline_stadium(df2, target[target["date"] < df["date"].max()], prior)
    assert np.allclose(b1[target["date"].to_numpy() < df["date"].max()], b1b)


def test_calibration_selection_reduces_loss():
    df, P = _frame(2000)
    Z = np.log(P) * 2.5  # 推しすぎのスコア
    y = df["y"].to_numpy()
    calib, cv = tk.select_calibration(Z, y, df["date"].to_numpy())
    assert set(cv) == {"none", "temperature", "temperature_bias"}
    assert calib["method"] != "none"
    assert tk.multi_logloss(tk.apply_calibration(Z, calib), y) < tk.multi_logloss(tk.softmax(Z), y)


@pytest.mark.skipif(not lightgbm_available(), reason="lightgbm を import できない")
def test_predict_tenkai_lock_rules():
    """前日版の確定(2回目は変わらない)と、直前版のレースごとの確定。"""
    from src.model.predict_tenkai import OUTPUT_COLUMNS, per_race_update, predict_frame

    lgb = tk.import_lightgbm()
    rng = np.random.default_rng(1)
    feats = ["c1_x", "c2_x"]
    X = pd.DataFrame(rng.normal(size=(300, 2)), columns=feats)
    y = rng.integers(0, 6, 300)
    booster = lgb.train({"objective": "multiclass", "num_class": 6, "verbosity": -1, "num_threads": 1}, lgb.Dataset(X, y), 5)
    meta = {"features": feats, "categorical": [], "best_iteration": 5, "model_id": "tenkai_preview_x", "variant": "preview",
            "calibration": {"method": "temperature", "params": {"T": 1.2}}}
    df = pd.DataFrame({"race_id": ["r1", "r2", "r3"], "c1_x": [0.1, 0.2, 0.3], "c2_x": [0, 1, 2], "c1_entry_number": [1.0, 2.0, 1.0]})
    for k in range(1, 7):
        df[f"c{k}_pv_exhibition_time"] = 6.8
    df.loc[1, "c4_pv_exhibition_time"] = np.nan  # r2 は直前情報が欠け
    now = pd.Timestamp("2026-11-06 10:00", tz="Asia/Tokyo")
    closed = pd.Series([now + pd.Timedelta(minutes=m) for m in (30, 30, -5)], index=["r1", "r2", "r3"])

    out = predict_frame(df, booster, meta, now)
    assert list(out.columns) == OUTPUT_COLUMNS
    assert np.allclose(out[tk.PROB_COLUMNS].sum(axis=1), 1)
    assert out["course1_entry"].tolist() == [1, 2, 1]

    o1, c1 = per_race_update(df, closed, now, booster, meta, None)
    assert c1 == {"added": 1, "locked": 0, "waiting": 1, "closed": 1} and o1["race_id"].tolist() == ["r1"]
    df.loc[1, "c4_pv_exhibition_time"] = 6.9
    later = now + pd.Timedelta(minutes=10)
    o2, c2 = per_race_update(df, closed, later, booster, meta, o1)
    assert c2 == {"added": 1, "locked": 1, "waiting": 0, "closed": 1}
    pd.testing.assert_frame_equal(o2[o2["race_id"] == "r1"].reset_index(drop=True), o1)
    assert "r3" not in set(o2["race_id"])
