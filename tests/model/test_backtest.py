"""回収率シミュレーションのテスト。"""

import numpy as np
import pandas as pd
import pytest

from src.model.backtest import bootstrap_roi_ci, max_drawdown, run_backtest, select_bets, settle_bets

P = [0.40, 0.20, 0.15, 0.12, 0.08, 0.05]


def _preds(race_ids, absent=None):
    absent = absent or {}
    rows = []
    for r in race_ids:
        for e in range(1, 7):
            rows.append({"race_id": r, "entry_number": e, "p_win": P[e - 1],
                         "is_absent": e in absent.get(r, []), "is_valid_result": True})
    return pd.DataFrame(rows)


def test_top_k_and_settlement():
    preds = _preds(["20261001-01-01", "20261001-01-02", "20261102-01-01"], absent={"20261001-01-02": [3]})
    payouts = pd.DataFrame(
        [
            ("20261001-01-01", "trifecta", "1-2-3", 1500, None),  # 1点目で的中
            ("20261001-01-02", "trifecta", "1-2-4", 2000, None),  # 3番は欠場 → 1-2-3 などは返還
            ("20261102-01-01", "trifecta", None, 70, "特払"),       # 特払
        ],
        columns=["race_id", "bet_type", "combination", "amount", "label"],
    )
    summary, bets = run_backtest(preds, payouts, "top_k", "trifecta", {"k": 2}, n_boot=100)
    # 上位2点は 1-2-3、1-3-2
    assert set(bets["combination"]) == {"1-2-3", "1-3-2"}
    assert summary["bets"] == 6 and summary["stake"] == 600
    b = bets.set_index(["race_id", "combination"])["return"]
    assert b[("20261001-01-01", "1-2-3")] == 1500
    assert b[("20261001-01-01", "1-3-2")] == 0
    assert b[("20261001-01-02", "1-2-3")] == 100  # 返還
    assert b[("20261001-01-02", "1-3-2")] == 100
    assert b[("20261102-01-01", "1-2-3")] == 70
    assert summary["return"] == 1500 + 200 + 140
    assert summary["roi"] == pytest.approx(1840 / 600)
    assert summary["hits"] == 1 and summary["refunds"] == 2
    assert summary["hit_rate"] == pytest.approx(1 / 3)
    assert [m["month"] for m in summary["by_month"]] == ["2026-10", "2026-11"]
    assert summary["leak_free"] is True


def test_dead_heat_payouts_summed():
    bets = pd.DataFrame({"race_id": ["r"], "bet_type": ["win"], "combination": ["1"]})
    payouts = pd.DataFrame(
        [("r", "win", "1", 250, None), ("r", "win", "2", 300, None)],
        columns=["race_id", "bet_type", "combination", "amount", "label"],
    )
    s = settle_bets(bets, payouts)
    assert s["return"].tolist() == [250] and s["hit"].tolist() == [True]


def test_select_strategies():
    probs = np.array([[0.5, 0.3, 0.2], [0.34, 0.33, 0.33]])
    pw = np.array([[0.6] + [0.08] * 5, [0.3] + [0.14] * 5])
    assert select_bets("top_k", probs, pw, k=1).tolist() == [[True, False, False], [True, False, False]]
    assert select_bets("prob_threshold", probs, pw, theta=0.3).sum() == 5
    odds = np.array([[1.5, 4.0, np.nan], [3.0, 3.0, 2.0]])
    ev = select_bets("ev_final", probs, pw, odds, tau=1.0)
    assert ev.tolist() == [[False, True, False], [True, False, False]]
    hc = select_bets("ev_final_haircut", probs, pw, odds, tau=1.0)
    assert hc.tolist() == [[False, True, False], [False, False, False]]
    cs = select_bets("confidence_skip", probs, pw, alpha=0.5, k=2)
    assert cs.tolist() == [[True, True, False], [False, False, False]]
    with pytest.raises(ValueError):
        select_bets("ev_final", probs, pw, None, tau=1.0)


def test_bootstrap_and_drawdown():
    rng = np.random.default_rng(0)
    stake = np.full(500, 500)
    ret = rng.choice([0, 2500], size=500, p=[0.8, 0.2])
    ci = bootstrap_roi_ci(stake, ret, n_boot=500, seed=1)
    roi = ret.sum() / stake.sum()
    assert ci[0] < roi < ci[1]
    assert bootstrap_roi_ci(stake, ret, n_boot=500, seed=1) == ci  # seed 固定で再現する
    assert bootstrap_roi_ci(np.array([]), np.array([])) is None
    assert max_drawdown(np.array([100, -300, 50, -100, 500])) == pytest.approx(350)
    assert max_drawdown(np.array([-100, -100])) == pytest.approx(200)


def test_ev_final_on_synthetic(synthetic_features, synthetic_market):
    payouts, odds = synthetic_market
    df = synthetic_features[synthetic_features["date"] >= "2026-05-01"]
    preds = df[["race_id", "entry_number", "is_absent", "is_valid_result"]].copy()
    preds["p_win"] = 1 / 6
    summary, bets = run_backtest(preds, payouts, "ev_final", "win", {"tau": 1.0}, odds=odds, n_boot=50)
    assert summary["leak_free"] is False
    assert (bets["ev_final"] >= 1.0).all()
    assert summary["races"] == df.loc[df["is_valid_result"], "race_id"].nunique()
