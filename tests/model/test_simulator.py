"""M-16 シミュレーター: 配分がフロント(docs/app/tests/ledger-test.js)と同じ結果になること、グリッドの形。"""

import inspect

import numpy as np
import pandas as pd
import pytest

from src.model import simulator as sim

B5 = [0.10, 0.08, 0.06, 0.04, 0.02]


@pytest.mark.parametrize("method,budget,expected", [
    ("equal", 1200, [300, 300, 200, 200, 200]),   # 端数は確率の高い順
    ("top", 900, [400, 200, 100, 100, 100]),      # 重み 3/2/1/1/1、余りは重みの大きい順
    ("prob", 300, [200, 100, 0, 0, 0]),           # 予算が点数×100円未満なら重みの小さい買い目は0円
    ("prob", 1500, [500, 400, 300, 200, 100]),    # 10:8:6:4:2
])
def test_allocation_matches_front_cases(method, budget, expected):
    assert sim.allocate_bets(method, B5, budget) == expected


def test_allocation_rules():
    for m in sim.ALLOCATIONS:
        for budget in (100, 700, 1000, 1300):
            a = sim.allocate_bets(m, [0.3, 0.2, 0.2, 0.05], budget)
            assert sum(a) <= budget and all(x % 100 == 0 for x in a)
    # 確率の並びが入れ替わっても、同じ確率の買い目には同じ規則(確率の高い順)で余りが付く
    assert sim.allocate_bets("equal", [0.02, 0.10], 300) == [100, 200]
    # 期待値・払戻をそろえる配分は入れない(オッズが要るため)
    with pytest.raises(ValueError):
        sim.allocate_bets("ev", B5, 1000)
    # 入力はこのレースの配分・確率・予算だけ(収支・連敗を受け取らない)
    params = list(inspect.signature(sim.allocate_bets).parameters)
    assert params == ["method", "probs", "budget"]


def _preds_payouts(n=60):
    rows, pay = [], []
    p = [0.55, 0.2, 0.1, 0.08, 0.05, 0.02]
    for r in range(n):
        rid = f"20261102-01-{r % 12 + 1:02d}" if r < 12 else f"2026110{2 + r // 12}-01-{r % 12 + 1:02d}"
        pr = p if r % 2 == 0 else [0.3, 0.25, 0.2, 0.1, 0.1, 0.05]
        for e in range(1, 7):
            rows.append({"race_id": rid, "entry_number": e, "p_win": pr[e - 1], "is_valid_result": True, "is_absent": False})
        pay += [(rid, "win", "1", 150, None), (rid, "exacta", "1-2", 500, None), (rid, "trifecta", "1-2-3", 1200, None)]
    return pd.DataFrame(rows), pd.DataFrame(pay, columns=["race_id", "bet_type", "combination", "amount", "label"])


def test_grid_shape_and_values(tmp_path):
    preds, pay = _preds_payouts()
    rows = sim.simulate_grid(preds, pay, 1000, tmp_path)
    assert len(rows) == 81 and len({r["id"] for r in rows}) == 81
    # 並びは券種 → 点数 → 見送り → 配分の固定順(回収率で並べ替えない)
    assert [r["bet_type"] for r in rows[:18]] == ["win"] * 18 and rows[0]["id"] == "win_k1_none_equal"
    win1 = rows[0]
    # 単勝1点・1,000円: 全レースで1号艇が1着、払戻150円/100円 → 回収率 1.5
    assert win1["stake"] == 60 * 1000 and win1["roi"] == pytest.approx(1.5) and win1["max_losing_streak"] == 0
    rough = next(r for r in rows if r["id"] == "win_k1_skip_mid_or_below_equal")
    assert rough["bet_races"] == 30  # 本命0.30のレースは見送り
    assert all(r["leak_free"] and r["level"] == "few" for r in rows)
    assert not any("最適" in r["label"] or "おすすめ" in r["label"] for r in rows)
