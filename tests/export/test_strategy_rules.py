"""E-9 / E-10: 推奨買い目の設定のルールと、共通のテストケース strategy_cases.json。"""

import glob
from pathlib import Path

import pytest

from src.export.build import iter_races
from src.export.io_util import read_json
from src.export.strategy_rules import (
    DEFAULT_SETTINGS, apply_settings, confidence_class, evaluate_picks, normalize_settings, settings_for_strategy,
    validate_settings,
)

REPO = Path(__file__).resolve().parents[2]
CASES = read_json(REPO / "docs" / "data" / "sample" / "strategy_cases.json")


def _by_id(group, cid):
    return next(c for c in CASES[group] if c["id"] == cid)


def _combs(res):
    return [f'{x["bet_type"]}:{x["combination"]}' for x in res]


def test_case_counts():
    assert len(CASES["cases"]) >= 20
    assert len(CASES["odds_cases"]) >= 10
    assert CASES["default_settings"] == DEFAULT_SETTINGS
    ids = [c["id"] for c in CASES["cases"] + CASES["odds_cases"]]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize("case", CASES["cases"], ids=lambda c: c["id"])
def test_cases(case):
    assert validate_settings(case["settings"]) == []
    assert apply_settings(case["race"], case["settings"]) == case["expected"]


@pytest.mark.parametrize("case", CASES["odds_cases"], ids=lambda c: c["id"])
def test_odds_cases(case):
    assert apply_settings(case["race"], case["settings"], case["odds_input"]) == case["expected"]
    ev = evaluate_picks(case["race"], case["odds_input"])
    for bt, rows in case["expected_evaluations"].items():
        for got, exp in zip(ev[bt], rows, strict=True):
            assert got["source"] == exp["source"] and got.get("ev_rank", "x") == exp.get("ev_rank", "x")
            if exp["ev"] is not None:
                assert abs(got["ev"] - exp["ev"]) < 1e-9


@pytest.mark.parametrize("case", CASES["normalize_cases"], ids=lambda c: c["id"])
def test_normalize_cases(case):
    assert normalize_settings(case["input"]) == case["expected"]
    assert validate_settings(case["expected"]) == []


# --- ケースの答えを手で確かめる(ケースを作った実装が間違っていても気づけるように) ---

def test_hand_checked_cases():
    assert _combs(_by_id("cases", "default")["expected"]) == [
        "trifecta:1-2-3", "trifecta:1-3-2", "trifecta:1-2-4", "trifecta:1-4-2", "trifecta:2-1-3"]
    assert _combs(_by_id("cases", "multi_bet_types")["expected"]) == [
        "win:1", "win:2", "win:3", "exacta:1-2", "exacta:1-3", "trifecta:1-2-3", "trifecta:1-3-2", "trifecta:1-2-4"]
    # 0.0200 ちょうどは残り、0.0199 は除かれる
    mp = _combs(_by_id("cases", "min_prob_2pct")["expected"])
    assert "trifecta:1-2-6" in mp and "trifecta:2-3-1" not in mp and len(mp) == 12
    # ランクB以上: A・B と、prob_only なら null(1-2-4)も残る
    assert _combs(_by_id("cases", "ev_b_prob_only")["expected"]) == [
        "trifecta:1-3-2", "trifecta:1-2-4", "trifecta:1-4-2", "trifecta:1-3-4", "trifecta:2-1-4"]
    assert _combs(_by_id("cases", "ev_b_skip")["expected"]) == [
        "trifecta:1-3-2", "trifecta:1-4-2", "trifecta:1-3-4", "trifecta:2-1-4", "trifecta:1-5-2"]
    assert _by_id("cases", "ev_a_no_odds_skip")["expected"] == []
    assert _by_id("cases", "skip_chu_on_chu")["expected"] == []
    assert len(_by_id("cases", "boundary_ara_0_35")["expected"]) == 5


def test_hand_checked_odds_cases():
    b = _by_id("odds_cases", "odds_boundary")
    ranks = {e["combination"]: e["ev_rank"] for e in b["expected_evaluations"]["trifecta"]}
    assert ranks == {"1-2-3": "C", "2-1-3": "A", "1-3-2": "B", "3-1-2": "C", "2-3-1": "C"}
    assert _combs(b["expected"]) == ["trifecta:2-1-3", "trifecta:1-3-2"]
    # 手入力 15.0 × 0.1012 = 1.518 → A。Worker の 1-2-3 は 8.9 × 0.1012 = 0.90 → C なので、手入力が効いている
    m = _by_id("odds_cases", "odds_manual_over_worker")
    assert m["expected"][0]["combination"] == "1-2-3"
    srcs = {e["combination"]: e["source"] for e in m["expected_evaluations"]["trifecta"]}
    assert srcs["1-2-3"] == "manual" and srcs["1-3-2"] == "worker"
    # Worker が3連単だけのとき、単勝・2連単は公開JSONの ev_rank を使う
    p = _by_id("odds_cases", "odds_worker_partial_falls_back")
    assert {e["source"] for e in p["expected_evaluations"]["win"]} == {"public"}
    assert {e["source"] for e in p["expected_evaluations"]["trifecta"]} == {"worker"}
    # Worker の空欄(欠場)は null
    n = _by_id("odds_cases", "odds_worker_null_skip")
    w6 = next(e for e in n["expected_evaluations"]["win"] if e["combination"] == "6")
    assert w6["source"] == "worker" and w6["ev_rank"] is None and w6["odds"] is None


def test_confidence_class():
    assert confidence_class(0.50) == "kata" and confidence_class(0.4999) == "chu"
    assert confidence_class(0.35) == "chu" and confidence_class(0.3499) == "ara"


def test_invalid_settings_raise():
    with pytest.raises(ValueError):
        apply_settings(CASES["cases"][0]["race"], {**DEFAULT_SETTINGS, "bet_types": {"win": 9}})


def _days():
    files = sorted(glob.glob(str(REPO / "docs" / "data" / "sample" / "days" / "*.json")))
    files += sorted(glob.glob(str(REPO / "docs" / "data" / "days" / "*.json")))
    return [read_json(f) for f in files]


def test_default_settings_match_export_recommended():
    """既定の設定(または export の戦略に対応する設定)で、export の recommended と一致する(07 4.2)。"""
    checked = 0
    for day in _days():
        settings = settings_for_strategy(day["strategy"])
        assert settings is not None, day["strategy"]
        for r in iter_races(day):
            pr = r["prediction"]
            if pr is None:
                continue
            exp = [{"bet_type": x["bet_type"], "combination": x["combination"]} for x in pr["recommended"]]
            assert apply_settings(pr, settings) == exp, r["race_id"]
            checked += 1
    assert checked > 200


def test_settings_for_strategy():
    assert settings_for_strategy({"name": "top_k", "bet_type": "trifecta", "k": 5}) == DEFAULT_SETTINGS
    s = settings_for_strategy({"name": "confidence_skip", "bet_type": "trifecta", "k": 5, "alpha": 0.35})
    assert s["skip_confidence"] == "ara"
    assert settings_for_strategy({"name": "confidence_skip", "bet_type": "trifecta", "k": 5, "alpha": 0.4}) is None
    assert settings_for_strategy({"name": "ev_final", "bet_type": "trifecta", "tau": 1.2}) is None
