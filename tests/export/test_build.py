"""build.py / probs.py の単体テスト。"""

import pytest

from src.export.build import DEFAULT_STRATEGY, build_prediction, compute_outcome, required_odds, \
    select_recommended, summarize
from src.export.probs import _internal_harville, combination_probs, normalize, top2_top3

P = [0.45, 0.18, 0.14, 0.11, 0.08, 0.04]


def test_required_odds_rounding():
    assert required_odds(0.4) == 2.5          # ちょうど割り切れるときは切り上げない
    assert required_odds(0.052) == 19.3       # 19.23 → 19.3
    assert required_odds(0.5) == 2.0
    assert required_odds(0.0) is None


def test_harville_sums():
    probs = _internal_harville(P)
    assert abs(sum(probs["trifecta"].values()) - 1) < 1e-9
    assert abs(sum(probs["exacta"].values()) - 1) < 1e-9
    assert len(probs["trifecta"]) == 120
    # 2連単の1着の周辺和は単勝と一致
    for i in range(1, 7):
        s = sum(v for k, v in probs["exacta"].items() if k.startswith(f"{i}-"))
        assert abs(s - P[i - 1]) < 1e-9
    top2, top3 = top2_top3(probs["trifecta"])
    assert abs(sum(top2) - 2) < 1e-9 and abs(sum(top3) - 3) < 1e-9


def test_combination_probs_matches_internal():
    """モデル担当の実装があってもなくても、内部実装と同じ値になる(γ=1)。"""
    a = combination_probs(P)
    b = combination_probs(P, use_model=False)
    for bt in ("win", "exacta", "trifecta"):
        for k, v in b[bt].items():
            assert abs(a[bt][k] - v) < 1e-9


def test_normalize_absent():
    q = normalize(P, absent=[1])
    assert q[0] == 0 and abs(sum(q) - 1) < 1e-12
    probs = _internal_harville(q)
    assert all(v == 0 for k, v in probs["trifecta"].items() if "1" in k.split("-"))


def _boats():
    return [{"entry": i, "racer_name": f"選手{i}", "racer_number": 4000 + i, "rank": "B1", "branch": "東京",
             "national_win_rate": 5.0, "local_win_rate": 5.0, "motor_top2": 40.0, "avg_st": 0.16,
             "flying_count": 0, "exhibition_time": None, "exhibition_course": None} for i in range(1, 7)]


def test_build_prediction_shape():
    pr = build_prediction(_boats(), P, variant="pre", predicted_at="2026-11-02T07:30:00+09:00",
                          strategy=DEFAULT_STRATEGY)
    assert [b["p_rank"] for b in pr["boats"]] == [1, 2, 3, 4, 5, 6]
    assert pr["confidence"] == 0.45
    assert len(pr["recommended"]) == 5
    assert pr["recommended"][0]["combination"] == pr["picks"]["trifecta"][0]["combination"]


def test_select_recommended_strategies():
    probs = _internal_harville(P)
    skip = {"name": "confidence_skip", "bet_type": "trifecta", "k": 3, "alpha": 0.5}
    assert select_recommended(skip, probs, confidence=0.45) == []
    assert len(select_recommended({**skip, "alpha": 0.4}, probs, confidence=0.45)) == 3
    th = select_recommended({"name": "prob_threshold", "bet_type": "exacta", "theta": 0.1}, probs, 0.45)
    assert all(x["prob"] >= 0.1 for x in th) and th
    with pytest.raises(ValueError):
        select_recommended({"name": "ev_final", "bet_type": "trifecta"}, probs, 0.45)


def _rec(*combs):
    return [{"bet_type": "trifecta", "combination": c, "prob": 0.05, "required_odds": 20.0} for c in combs]


def test_outcome_hit_and_miss():
    pay = {"trifecta": [{"combination": "1-2-3", "amount": 1230}]}
    oc = compute_outcome(_rec("1-2-3", "1-3-2"), pay, [])
    assert oc == {"bets": 2, "hits": 1, "stake": 200, "return": 1230}


def test_outcome_refund_for_absent():
    pay = {"trifecta": [{"combination": "1-2-3", "amount": 1230}]}
    oc = compute_outcome(_rec("1-2-4", "1-2-3", "1-3-2"), pay, [4])
    assert oc == {"bets": 3, "hits": 1, "stake": 300, "return": 1330, "refunded": 1}


def test_outcome_special_payout():
    pay = {"trifecta": [{"combination": None, "amount": 100, "label": "不成立"}]}
    oc = compute_outcome(_rec("1-2-3", "1-3-2"), pay, [])
    assert oc == {"bets": 2, "hits": 0, "stake": 200, "return": 200}


def test_summarize_none_without_results():
    assert summarize([{"result": None}]) is None


def test_required_odds_matches_model():
    mc = pytest.importorskip("src.model.combinations")
    for prob in (0.4, 0.05, 0.052, 0.0137, 0.3333, 0.0009):
        assert required_odds(prob) == mc.required_odds_display(prob)


def test_short_labels():
    from src.export.build import SHORT_LABEL_MAX, strategy_short_label, with_short_label
    assert strategy_short_label("top_k", {"bet_type": "trifecta", "k": 5}) == "3連単 上位5点"
    assert strategy_short_label("confidence_skip", {"bet_type": "trifecta", "k": 5, "alpha": 0.35}) \
        == "3連単 上位5点・荒れ見送り"
    assert strategy_short_label("prob_threshold", {"bet_type": "trifecta", "theta": 0.05}) == "3連単 確率5%以上"
    assert strategy_short_label("ev_final", {"bet_type": "win", "tau": 1.2}) == "単勝 EV1.2以上"
    assert len(strategy_short_label("unknown_strategy_name", {"bet_type": "trifecta"})) <= SHORT_LABEL_MAX
    s = with_short_label({"name": "top_k", "bet_type": "exacta", "k": 3, "description": "x"})
    assert list(s) == ["name", "bet_type", "k", "short_label", "description"]
    assert s["short_label"] == "2連単 上位3点"


def test_gamma_is_passed_to_model_combinations():
    pytest.importorskip("src.model.combinations")
    a = combination_probs(P, gamma2=0.8, gamma3=0.7)
    b = combination_probs(P)
    assert abs(sum(a["trifecta"].values()) - 1) < 1e-9
    assert a["trifecta"]["1-2-3"] != pytest.approx(b["trifecta"]["1-2-3"])
    with pytest.raises(RuntimeError):
        combination_probs(P, use_model=False, gamma2=0.8)
