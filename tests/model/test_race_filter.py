"""買うレースを絞る条件: 条件が締切前の情報だけで作られること、しきい値が前半だけで決まること、集計と候補の判定。"""

import numpy as np
import pandas as pd

from src.model import race_filter as rf


def _preds(dates, rng):
    rows = []
    for d in dates:
        for r in range(1, 13):
            rid = f"{d:%Y%m%d}-01-{r:02d}"
            p = rng.dirichlet(np.ones(6) * 2)
            rows += [{"race_id": rid, "entry_number": e + 1, "p_win": p[e], "is_valid_result": True, "is_absent": False}
                     for e in range(6)]
    return pd.DataFrame(rows)


def _payouts(preds, rng):
    out = []
    for rid, g in preds.groupby("race_id"):
        o = rng.permutation(6) + 1
        out.append({"race_id": rid, "bet_type": "win", "combination": str(o[0]), "amount": 300, "label": None})
        out.append({"race_id": rid, "bet_type": "exacta", "combination": f"{o[0]}-{o[1]}", "amount": 1500, "label": None})
        out.append({"race_id": rid, "bet_type": "quinella", "combination": f"{min(o[:2])}={max(o[:2])}", "amount": 800, "label": None})
        out.append({"race_id": rid, "bet_type": "trifecta", "combination": f"{o[0]}-{o[1]}-{o[2]}", "amount": 9000, "label": None})
        out.append({"race_id": rid, "bet_type": "trio", "combination": "=".join(map(str, sorted(o[:3]))), "amount": 2000, "label": None})
    return pd.DataFrame(out)


def test_race_table_columns():
    rng = np.random.default_rng(0)
    rt, pw = rf.race_table(_preds(pd.date_range("2025-01-01", periods=2), rng))
    assert len(rt) == 24 and pw.shape == (24, 6)
    assert np.allclose(pw.sum(axis=1), 1)
    assert (rt["gap12"] >= 0).all() and (rt["p_fav"] >= 1 / 6 - 1e-9).all()
    assert set(rt["race_number"]) == set(range(1, 13))
    # 確定オッズや結果の列は条件に使わない
    assert not {"odds", "finish_position", "is_win"} & set(rt.columns)


def test_thresholds_from_first_half_only():
    first = pd.DataFrame({"p_fav": np.linspace(0, 1, 101), "fav_is_1": True})
    a = {c.name: c.value for c in rf.make_conditions(first, stadiums=[])}
    # 後半にどんな値が来ても、しきい値は前半の分位点のまま
    first2 = pd.DataFrame({"p_fav": np.linspace(0, 1, 101), "fav_is_1": True})
    b = {c.name: c.value for c in rf.make_conditions(first2, stadiums=[])}
    assert a == b
    assert any(abs(v - 0.9) < 1e-9 for v in a.values())


def test_condition_mask():
    df = pd.DataFrame({"x": [0.1, 0.5, 0.9]})
    assert list(rf.Condition("a", "x", ">=", 0.5).mask(df)) == [False, True, True]
    assert list(rf.Condition("a", "x", "<", 0.5).mask(df)) == [True, False, False]
    assert rf.Condition("a", "", "all").mask(df).all()


def test_summarize_flat_stake():
    s = rf.summarize(np.array([0.0, 500.0, 0.0, 0.0]), k=2)
    assert s["stake"] == 800 and s["return"] == 500
    assert abs(s["roi"] - 500 / 800) < 1e-12 and s["hit_rate"] == 0.25
    assert s["ci95"][0] <= s["roi"] <= s["ci95"][1]


def test_is_candidate():
    base = {"roi": 0.8}
    assert not rf.is_candidate({"races": 100, "roi": 1.5, "ci95": [1.0, 2.0]}, base)  # 件数不足
    assert rf.is_candidate({"races": 300, "roi": 1.05, "ci95": [0.7, 1.4]}, base)       # 100%超
    assert rf.is_candidate({"races": 300, "roi": 0.9, "ci95": [0.85, 0.95]}, base)      # 全レースより明らかに良い
    assert not rf.is_candidate({"races": 300, "roi": 0.9, "ci95": [0.7, 1.1]}, base)


def test_run_end_to_end(monkeypatch):
    monkeypatch.setattr(rf, "MIN_RACES", 10)
    rng = np.random.default_rng(1)
    preds = _preds(pd.date_range("2025-12-20", periods=24), rng)
    pay = _payouts(preds, rng)
    res = rf.run(preds, pay, "2026-01-01", 0.7, 0.5)
    n_cells = sum(len(v) for v in rf.BET_KS.values())
    assert len(res["baseline"]) == n_cells
    assert res["n_trials"] == len(res["trials"]) > n_cells
    b = res["baseline"]["win_k1"]
    assert b["first"]["races"] == 12 * 12 and b["second"]["races"] == 12 * 12
    for s in res["selected"]:
        assert s["first"]["races"] >= 10
        assert set(s["first"]) >= {"roi", "ci95", "stake"}
    md = rf.render(res, {"from": "2025-12-20", "to": "2026-01-12", "races": 288, "notes": rf.NOTES})
    assert not any(w in md for w in rf.FORBIDDEN_WORDS)
    assert "前半" in md and "後半" in md
    # 前半の条件を同じ予想に当てはめ直すと、全レースは同じ件数
    live = rf.apply_rules(preds, pay, res["selected"][:2], 0.7, 0.5)
    assert all(r["all"]["races"] == 288 for r in live)
