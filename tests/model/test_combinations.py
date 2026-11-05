"""src/model/combinations.py のテスト(spec/03 8章)。"""

import math
from itertools import permutations

import numpy as np
import pytest

from src.model import combinations as cb

P = [0.40, 0.20, 0.15, 0.12, 0.08, 0.05]


def _harville_trifecta(p, i, j, k):
    """spec/03 2.2章の式をそのまま書いたもの(枠番は1始まり)。"""
    pi, pj, pk = p[i - 1], p[j - 1], p[k - 1]
    return pi * pj / (1 - pi) * pk / (1 - pi - pj)


def test_labels():
    assert len(cb.combination_labels("win")) == 6
    assert len(cb.combination_labels("place")) == 6
    assert len(cb.combination_labels("exacta")) == 30
    assert len(cb.combination_labels("quinella")) == 15
    assert len(cb.combination_labels("quinella_place")) == 15
    assert len(cb.combination_labels("trifecta")) == 120
    assert len(cb.combination_labels("trio")) == 20
    assert cb.combination_labels("trifecta")[0] == "1-2-3"
    assert cb.combination_labels("trio")[0] == "1=2=3"
    # 順不同は昇順で書く
    for label in cb.combination_labels("trio") + cb.combination_labels("quinella"):
        nums = [int(x) for x in label.split("=")]
        assert nums == sorted(nums)


def test_all_bet_types_sum():
    probs = cb.combination_probs(P)
    assert set(probs) == set(cb.BET_TYPES)
    expected_sum = {
        "win": 1, "exacta": 1, "quinella": 1, "trifecta": 1, "trio": 1,
        "place": 2, "quinella_place": 3,
    }
    for bt, s in expected_sum.items():
        assert sum(probs[bt].values()) == pytest.approx(s, abs=1e-12), bt
        assert all(v >= 0 for v in probs[bt].values())


def test_matches_harville_formula():
    probs = cb.combination_probs(P)
    for i, j, k in permutations(range(1, 7), 3):
        assert probs["trifecta"][f"{i}-{j}-{k}"] == pytest.approx(_harville_trifecta(P, i, j, k), rel=1e-12)
    for i, j in permutations(range(1, 7), 2):
        assert probs["exacta"][f"{i}-{j}"] == pytest.approx(P[i - 1] * P[j - 1] / (1 - P[i - 1]), rel=1e-12)


def test_marginals_consistent():
    probs = cb.combination_probs(P)
    ex, tri = probs["exacta"], probs["trifecta"]
    for i in range(1, 7):
        # 2連単の1着の周辺和が単勝と一致
        assert sum(ex[f"{i}-{j}"] for j in range(1, 7) if j != i) == pytest.approx(P[i - 1], abs=1e-12)
        for j in range(1, 7):
            if i == j:
                continue
            # 3連単の周辺和が2連単と一致
            s = sum(tri[f"{i}-{j}-{k}"] for k in range(1, 7) if k not in (i, j))
            assert s == pytest.approx(ex[f"{i}-{j}"], abs=1e-12)
    # 2連複 = 2連単の両向き、3連複 = 6通りの和
    assert probs["quinella"]["1=2"] == pytest.approx(ex["1-2"] + ex["2-1"])
    assert probs["trio"]["1=2=3"] == pytest.approx(sum(tri["-".join(map(str, pm))] for pm in permutations((1, 2, 3))))
    # 複勝 = 2着以内
    assert probs["place"]["3"] == pytest.approx(sum(ex[f"3-{j}"] + ex[f"{j}-3"] for j in (1, 2, 4, 5, 6)))
    # 拡連複 = 3連単のうち2艇とも3着以内
    qp = sum(v for c, v in tri.items() if {"1", "2"} <= set(c.split("-")))
    assert probs["quinella_place"]["1=2"] == pytest.approx(qp)


def test_finish_probs():
    fp = cb.finish_probs(P)
    assert sum(fp["p_win"]) == pytest.approx(1)
    assert sum(fp["p_top2"]) == pytest.approx(2)
    assert sum(fp["p_top3"]) == pytest.approx(3)
    probs = cb.combination_probs(P, bet_types=["place"])
    for i in range(6):
        assert fp["p_top2"][i] == pytest.approx(probs["place"][str(i + 1)])
        assert fp["p_win"][i] <= fp["p_top2"][i] <= fp["p_top3"][i] <= 1


def test_absent_renormalization():
    probs = cb.combination_probs(P, absent_entries=[2])
    renorm = np.array(P) / (1 - P[1])
    renorm[1] = 0
    assert probs["win"]["2"] == 0
    assert probs["win"]["1"] == pytest.approx(renorm[0])
    for bt in ("win", "exacta", "quinella", "trifecta", "trio"):
        assert sum(probs[bt].values()) == pytest.approx(1, abs=1e-12)
    # 欠場艇を含む組合せは0
    for bt in ("exacta", "trifecta", "trio", "quinella"):
        for c, v in probs[bt].items():
            if "2" in c.replace("-", " ").replace("=", " ").split():
                assert v == 0
    # dict で渡して欠けている枠番は欠場と同じ
    d = {e: P[e - 1] for e in (1, 3, 4, 5, 6)}
    probs2 = cb.combination_probs(d)
    assert probs2["trifecta"] == pytest.approx(probs["trifecta"])


def test_absent_leaves_three_boats():
    probs = cb.combination_probs(P, absent_entries=[4, 5, 6])
    assert sum(probs["trifecta"].values()) == pytest.approx(1)
    assert probs["trio"]["1=2=3"] == pytest.approx(1)
    assert sum(probs["quinella_place"].values()) == pytest.approx(3)


def test_gamma_correction():
    probs = cb.combination_probs(P, gamma2=0.8, gamma3=0.7)
    base = cb.combination_probs(P)
    for bt in ("exacta", "trifecta", "trio", "quinella"):
        assert sum(probs[bt].values()) == pytest.approx(1, abs=1e-12)
    # 1着の周辺は γ に関係なく p_win
    for i in range(1, 7):
        assert sum(v for c, v in probs["exacta"].items() if c.startswith(f"{i}-")) == pytest.approx(P[i - 1])
    # γ<1 は2着・3着の強さの差を縮める(人気艇の2着確率が下がる)
    assert probs["exacta"]["2-1"] < base["exacta"]["2-1"]
    # gamma=1 は Harville と一致
    assert cb.combination_probs(P, gamma2=1.0, gamma3=1.0) == base


def test_vector_matches_scalar():
    rng = np.random.default_rng(0)
    mat = rng.dirichlet(np.ones(6), size=5)
    mat[2, 3] = 0
    mat[2] /= mat[2].sum()
    for bt in cb.BET_TYPES:
        m = cb.combination_prob_matrix(mat, bt, gamma2=0.9, gamma3=1.1)
        assert m.shape == (5, len(cb.combination_labels(bt)))
        for r in range(5):
            d = cb.combination_probs(mat[r], gamma2=0.9, gamma3=1.1, bet_types=[bt])[bt]
            assert list(d.values()) == pytest.approx(list(m[r]), abs=1e-14)
    fp = cb.finish_prob_matrix(mat)
    assert np.allclose(fp["p_top3"].sum(axis=1), 3)


def test_top_combinations():
    probs = cb.combination_probs(P)["trifecta"]
    top = cb.top_combinations(probs, 5)
    assert len(top) == 5
    assert top[0][0] == "1-2-3"
    assert [v for _, v in top] == sorted([v for _, v in top], reverse=True)
    # 同じ確率は元の順(辞書順)
    tie = cb.top_combinations({"1": 0.5, "2": 0.5}, None)
    assert [c for c, _ in tie] == ["1", "2"]


def test_required_odds():
    assert cb.required_odds(0.25) == pytest.approx(4.0)
    assert cb.required_odds(0) == math.inf
    assert cb.required_odds_display(0.05) == 20.0  # 浮動小数の誤差で20.1にならない
    assert cb.required_odds_display(0.052) == 19.3  # 19.23… → 19.3
    assert cb.required_odds_display(0.4120) == 2.5  # 2.427… → 2.5
    assert cb.required_odds_display(0) == math.inf
    assert cb.expected_value(0.05, 25.0) == pytest.approx(1.25)
    with pytest.raises(ValueError):
        cb.required_odds(-0.1)


def test_invalid_inputs():
    with pytest.raises(ValueError):
        cb.normalize_p_win([0.5, 0.5])
    with pytest.raises(ValueError):
        cb.normalize_p_win([0.2, 0.2, 0.2, 0.2, 0.2, float("nan")])
    with pytest.raises(ValueError):
        cb.normalize_p_win([-0.1, 0.3, 0.2, 0.2, 0.2, 0.2])
    with pytest.raises(ValueError):
        cb.normalize_p_win([1, 0, 0, 0, 0, 0], absent_entries=[1])
    with pytest.raises(ValueError):
        cb.normalize_p_win(P, absent_entries=[7])
    with pytest.raises(ValueError):
        cb.combination_probs(P, gamma2=0)
    with pytest.raises(ValueError):
        cb.combination_probs(P, bet_types=["bracket"])


def test_normalize_does_not_mutate_input():
    arr = np.array(P)
    cb.normalize_p_win(arr, absent_entries=[1])
    assert arr[0] == P[0]
