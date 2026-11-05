"""較正・γ探索・latest の置き場所のテスト(lightgbm を使わない)。"""

import numpy as np
import pandas as pd
import pytest

from src.model import calibration as cal
from src.model.lgbm import latest_path, resolve_model_id, write_latest

PRIOR = np.array([0.5, 0.15, 0.13, 0.1, 0.07, 0.05])


def _sim(n=3000, seed=0, overconf=2.0):
    """スコアが真の logit の overconf 倍になっている(本命を推しすぎる)データ。"""
    rng = np.random.default_rng(seed)
    true = rng.normal(0, 1, (n, 6)) + np.log(PRIOR)
    p = np.exp(true) / np.exp(true).sum(axis=1, keepdims=True)
    winner = np.array([rng.choice(6, p=r) for r in p])
    dates = np.array(pd.date_range("2026-09-01", periods=30).values)[rng.integers(0, 30, n)]
    return true * overconf, winner, dates


@pytest.mark.parametrize("method", cal.METHODS)
def test_methods_sum_to_one_and_improve(method):
    S, w, _ = _sim()
    c = cal.fit_matrix(method, S, w, PRIOR)
    P = cal.apply_matrix(S, c)
    assert np.allclose(P.sum(axis=1), 1)
    raw = cal._softmax_rows(S)
    assert cal._nll(P, w) < cal._nll(raw, w)  # 推しすぎを直せる


def test_select_and_apply_rows():
    S, w, d = _sim()
    best, cv = cal.select_calibration(S, w, d, PRIOR)
    assert set(cv) == set(cal.METHODS) and best["method"] == min(cv, key=cv.get)
    # 行形式と行列形式が一致する(欠けた艇があっても合計1)
    df = pd.DataFrame({"race_id": np.repeat(np.arange(5), 6), "entry_number": np.tile(np.arange(1, 7), 5),
                       "s": S[:5].reshape(-1)}).iloc[:-1]
    for method in cal.METHODS:
        c = cal.fit_matrix(method, S, w, PRIOR)
        p = cal.apply_rows(df["race_id"].to_numpy(), df["entry_number"].to_numpy(), df["s"].to_numpy(), c)
        assert np.allclose(pd.Series(p).groupby(df["race_id"].to_numpy()).sum(), 1)
        if method != "prior_shrink":
            assert p[:6] == pytest.approx(cal.apply_matrix(S[:1], c)[0])


def test_calibration_from_meta_default():
    assert cal.calibration_from_meta({"temperature": 1.3}) == {"method": "temperature", "params": {"T": 1.3}}


def test_search_gamma():
    P = np.tile([[0.5, 0.2, 0.12, 0.09, 0.06, 0.03]], (200, 1))
    F = np.tile([[1, 2, 3, 4, 5, 6]], (200, 1)).astype(float)
    ok, idx = cal.trifecta_index(F)
    assert ok.all() and (idx == 0).all()
    g2, g3, ll = cal.search_gamma(P, idx)
    assert ll <= cal.trifecta_logloss(P, idx, 1.0, 1.0)
    assert 0.5 <= g2 <= 1.2 and 0.5 <= g3 <= 1.2


def test_latest_per_variant(tmp_path):
    write_latest("pre_x", "pre", tmp_path)
    write_latest("preview_y", "preview", tmp_path)
    assert latest_path("pre", tmp_path) == tmp_path / "latest"  # export が読む場所は変えない
    assert resolve_model_id("latest", tmp_path, "pre") == "pre_x"
    assert resolve_model_id("latest", tmp_path, "preview") == "preview_y"


def test_variant_of_model_id_and_paths(tmp_path):
    from src.model.config import is_preview_variant, pre_variant_of, variant_of_model_id
    from src.model.lgbm import model_dir

    assert variant_of_model_id("pre_20261104_db90") == "pre"
    assert variant_of_model_id("pre_v2_20261105_6f88") == "pre_v2"
    assert variant_of_model_id("preview_v2_20261105_ef72") == "preview_v2"
    assert model_dir("preview_v2_20261105_ef72", tmp_path) == tmp_path / "preview_v2" / "preview_v2_20261105_ef72"
    assert pre_variant_of("preview_v2") == "pre_v2" and is_preview_variant("preview_v2")
    write_latest("pre_v2_x", "pre_v2", tmp_path)
    assert latest_path("pre_v2", tmp_path) == tmp_path / "pre_v2" / "latest"
