"""LightGBM の学習のテスト。lightgbm を import できない環境(libgomp が無いなど)では skip する。"""

import json

import numpy as np
import pandas as pd
import pytest

from src.model.lgbm import lightgbm_available

pytestmark = pytest.mark.skipif(not lightgbm_available(), reason="lightgbm を import できない(libgomp1 が無い可能性)")


@pytest.fixture(scope="module")
def trained(synthetic_features, small_periods):
    from src.model.train import train_model

    return train_model(
        synthetic_features, "pre", small_periods,
        params={"min_data_in_leaf": 50}, num_boost_round=200, early_stopping_rounds=30,
    )


def test_train_meta(trained):
    booster, meta = trained
    for key in ("model_id", "variant", "periods", "features", "categorical", "params", "best_iteration",
                "temperature", "data_hash", "seed", "train_until", "valid_win_logloss", "valid_win_logloss_b1"):
        assert key in meta
    assert meta["model_id"].startswith("pre_")
    # spec/03 3章「meta.json の必須キー」
    assert meta["train_until"] == meta["periods"]["train_to"]
    assert "harville_gamma" in meta and meta["harville_gamma"] is None  # MVP は補正なし
    json.dumps(meta)  # JSON に書ける(harville_gamma は null になる)
    assert 0.2 <= meta["temperature"] <= 5.0
    assert meta["valid_win_logloss"] <= meta["valid_win_logloss_t1"] + 1e-9  # T 調整で悪くならない
    assert meta["valid_win_logloss"] < np.log(6)


def test_reproducible(synthetic_features, trained, small_periods):
    from src.model.train import train_model

    _, meta2 = train_model(
        synthetic_features, "pre", small_periods,
        params={"min_data_in_leaf": 50}, num_boost_round=200, early_stopping_rounds=30,
    )
    _, meta = trained
    assert meta2["data_hash"] == meta["data_hash"]
    assert meta2["best_iteration"] == meta["best_iteration"]
    assert meta2["temperature"] == pytest.approx(meta["temperature"])


def test_save_load_and_evaluate(tmp_path, synthetic_features, synthetic_market, trained, small_periods):
    from src.model.data import race_softmax
    from src.model.evaluate import evaluate_all
    from src.model.lgbm import load_model, raw_scores
    from src.model.train import save_model

    booster, meta = trained
    d = save_model(booster, meta, tmp_path, set_latest=True)
    assert (d / "model.txt").exists()
    assert json.loads((d / "meta.json").read_text())["model_id"] == meta["model_id"]
    assert (tmp_path / "latest").read_text().strip() == meta["model_id"]

    b2, meta2 = load_model("latest", tmp_path)
    test = synthetic_features[synthetic_features["date"] >= "2026-05-01"]
    s1 = raw_scores(booster, test, meta)
    s2 = raw_scores(b2, test, meta2)
    assert s1 == pytest.approx(s2)  # 保存・読み込みで予測が変わらない(カテゴリの対応も含む)
    p = race_softmax(test["race_id"].to_numpy(), s2, meta2["temperature"])
    sums = pd.Series(p).groupby(test["race_id"].to_numpy()).sum()
    assert np.allclose(sums, 1, atol=1e-6)

    payouts, _ = synthetic_market
    results, preds = evaluate_all(synthetic_features, small_periods, (b2, meta2), payouts, n_boot=50)
    name = f"LightGBM({meta['model_id']})"
    assert name in results["test"]
    assert "win_logloss" in results["test"][name]
    assert {"score", "p_win", "p_win_b0", "p_win_b1"} <= set(preds["test"].columns)


def test_set_latest_only_when_beats_b1(tmp_path, trained):
    from src.model.train import save_model

    booster, meta = trained
    save_model(booster, {**meta, "beats_b1": False}, tmp_path)
    assert not (tmp_path / "latest").exists()
    save_model(booster, {**meta, "beats_b1": True}, tmp_path)
    assert (tmp_path / "latest").exists()


def test_predict_frame(synthetic_features, trained):
    from src.model.predict import OUTPUT_COLUMNS, predict_frame

    booster, meta = trained
    today = synthetic_features[synthetic_features["date"] == synthetic_features["date"].max()].copy()
    today[["finish_position", "is_win", "is_top2", "is_top3", "is_valid_result", "is_absent"]] = None
    out = predict_frame(today, booster, meta)
    assert list(out.columns) == OUTPUT_COLUMNS
    g = out.groupby("race_id")
    assert np.allclose(g["p_win"].sum(), 1, atol=1e-6)
    assert np.allclose(g["p_top2"].sum(), 2, atol=1e-6)
    assert np.allclose(g["p_top3"].sum(), 3, atol=1e-6)
    assert (out["p_win"] <= out["p_top2"]).all() and (out["p_top2"] <= out["p_top3"] + 1e-12).all()


def test_predict_lock_and_contrib(tmp_path, synthetic_features, trained):
    """M-9: 2回目の実行で確定版が変わらない。--force は旧版を superseded/ へ退避する。M-10: 根拠トップ3。"""
    from src.model.predict import CONTRIB_COLUMNS, contrib_frame, predict_frame, write_locked

    booster, meta = trained
    today = synthetic_features[synthetic_features["date"] == synthetic_features["date"].max()].copy()
    out1 = predict_frame(today, booster, meta)
    c1 = contrib_frame(today, booster, meta)
    assert list(c1.columns) == CONTRIB_COLUMNS
    assert (c1.groupby(["race_id", "entry_number"]).size() <= 3).all()
    assert c1.groupby(["race_id", "entry_number"]).size().min() >= 1
    # 絶対値の降順
    for _, g in c1.groupby(["race_id", "entry_number"]):
        assert list(g.sort_values("rank")["contribution"].abs()) == sorted(g["contribution"].abs(), reverse=True)
    assert (out1["locked_at"] == out1["predicted_at"]).all()

    assert write_locked(out1, c1, tmp_path, "20260531", "pre") == "written"
    path = tmp_path / "20260531_pre.parquet"
    first = pd.read_parquet(path)
    first_c = pd.read_parquet(tmp_path / "20260531_pre_contrib.parquet")

    out2 = predict_frame(today, booster, {**meta, "model_id": "pre_other"})
    assert write_locked(out2, c1, tmp_path, "20260531", "pre") == "skipped"
    pd.testing.assert_frame_equal(pd.read_parquet(path), first)  # 確定版は変わらない
    pd.testing.assert_frame_equal(pd.read_parquet(tmp_path / "20260531_pre_contrib.parquet"), first_c)

    assert write_locked(out2, c1, tmp_path, "20260531", "pre", force=True) == "replaced"
    sup = tmp_path / "superseded" / f"20260531_pre_{meta['model_id']}.parquet"
    pd.testing.assert_frame_equal(pd.read_parquet(sup), first)
    assert (tmp_path / "superseded" / f"20260531_pre_{meta['model_id']}_contrib.parquet").exists()
    assert (pd.read_parquet(path)["model_id"] == "pre_other").all()


def test_predict_cli_skips_when_locked(tmp_path, monkeypatch, capsys):
    """CLI: 確定版があればモデルを読まずに終了コード0で終わる。"""
    import sys

    from src.model import predict

    (tmp_path / "20260531_pre.parquet").write_bytes(b"locked")
    monkeypatch.setattr(sys, "argv", ["predict", "--date", "2026-05-31", "--out-dir", str(tmp_path),
                                      "--models-dir", str(tmp_path / "none")])
    predict.main()
    assert "確定済み" in capsys.readouterr().out
    assert (tmp_path / "20260531_pre.parquet").read_bytes() == b"locked"
