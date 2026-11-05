"""schema.py の検証が、壊れた JSON を正しく落とすことを確かめる。"""

import copy

import pytest

from src.export import make_sample
from src.export.build import iter_races
from src.export.schema import SchemaError, assert_valid, check_forbidden, validate_day, validate_index, \
    validate_performance


@pytest.fixture(scope="module")
def sample(tmp_path_factory):
    return make_sample.make_all(tmp_path_factory.mktemp("s"))


@pytest.fixture
def day(sample):
    return copy.deepcopy(sample["days"]["2026-11-02"])


def _race(day, state):
    return next(r for r in iter_races(day) if r["state"] == state)


def test_assert_valid_raises(day):
    del day["status"]
    with pytest.raises(SchemaError) as e:
        assert_valid("day", day)
    assert "status" in str(e.value)


def test_race_id_mismatch(day):
    day["stadiums"][0]["races"][0]["race_id"] = "20261102-99-01"
    assert any("race_id" in e for e in validate_day(day))


def test_race_order_and_stadium_order(day):
    day["stadiums"].reverse()
    assert any("昇順" in e for e in validate_day(day))


def test_state_consistency(day):
    r = _race(day, "scheduled")
    r["state"] = "finished"
    assert any("result が必要" in e for e in validate_day(day))


def test_no_prediction_must_have_null_prediction(day):
    r = _race(day, "finished")
    r["state"] = "no_prediction"
    assert validate_day(day)


def test_p_win_sum(day):
    r = _race(day, "finished")
    r["prediction"]["boats"][0]["p_win"] += 0.05
    assert validate_day(day)


def test_picks_order_and_limits(day):
    r = _race(day, "scheduled")
    picks = r["prediction"]["picks"]["trifecta"]
    picks.reverse()
    assert any("降順" in e for e in validate_day(day))
    day2 = copy.deepcopy(day)
    r2 = _race(day2, "scheduled")
    r2["prediction"]["picks"]["exacta"] *= 2
    assert validate_day(day2)


def test_required_odds_must_match_prob(day):
    r = _race(day, "scheduled")
    r["prediction"]["picks"]["win"][0]["required_odds"] = 99.9
    assert any("required_odds" in e for e in validate_day(day))


def test_payouts_only_exported_bet_types(day):
    r = _race(day, "finished")
    r["result"]["payouts"]["trio"] = [{"combination": "1=2=3", "amount": 500}]
    assert any("win / exacta / trifecta" in e for e in validate_day(day))


def test_day_summary_must_match(day):
    day["day_summary"]["return"] += 100
    assert validate_day(day)


def test_final_odds_only_recommended(day):
    r = next(r for r in iter_races(day) if r["state"] == "finished" and r["prediction"]["recommended"])
    r["result"]["final_odds"] = [{"bet_type": "trifecta", "combination": "6-5-4", "ev_rank": "A"}]
    if any(x["combination"] == "6-5-4" for x in r["prediction"]["recommended"]):
        pytest.skip("偶然 6-5-4 が推奨に入っている")
    assert any("推奨買い目の分だけ" in e for e in validate_day(day))


@pytest.mark.parametrize("key", ["age", "weight", "rank_number_source", "_source", "remarks", "refunds"])
def test_forbidden_keys(day, key):
    day["stadiums"][0][key] = "x"
    assert check_forbidden(day)
    assert validate_day(day)


def test_odds_table_is_forbidden(day):
    r = _race(day, "finished")
    r["result"]["odds"] = {"trifecta": [{"combination": "1-2-3", "odds": 8.1}]}
    assert any("odds" in e for e in check_forbidden(day))


def test_odds_numbers_only_in_local(sample):
    """公開JSONには odds・ev の数値が無い。ローカルJSONでは picks・recommended・final_odds の中だけ許す(E-7・E-8)。"""
    final = sample["days"]["2026-11-01"]
    assert check_forbidden(final) == []
    assert any(r["result"]["final_odds"] for r in iter_races(final))
    local = sample["local_days"]["2026-11-01"]
    assert check_forbidden(local) != []                 # 公開JSONとしては不合格
    assert check_forbidden(local, local=True) == []
    assert validate_day(local, local=True) == []
    bad = copy.deepcopy(local)
    bad["stadiums"][0]["races"][0]["result"]["odds"] = 1.0   # 許した場所以外
    assert check_forbidden(bad, local=True)


def test_index_order(sample):
    idx = copy.deepcopy(sample["index"])
    idx["dates"].reverse()
    assert validate_index(idx)


def test_performance_ci(sample):
    perf = copy.deepcopy(sample["performance"])
    perf["backtest"]["strategies"][0]["roi_ci95"] = [1.2, 0.8]
    assert validate_performance(perf)
    perf["backtest"]["strategies"][0]["roi_ci95"] = [0.8]
    assert validate_performance(perf)


def test_datetime_needs_jst(day):
    day["generated_at"] = "2026-11-02T06:05:00Z"
    assert any("generated_at" in e for e in validate_day(day))


def test_short_label_length(day, sample):
    day["strategy"]["short_label"] = "あ" * 15
    assert any("short_label" in e for e in validate_day(day))
    perf = copy.deepcopy(sample["performance"])
    assert perf["live"]["strategy_label"] and all(s["short_label"] for s in perf["backtest"]["strategies"])
    perf["backtest"]["strategies"][0]["short_label"] = ""
    assert validate_performance(perf)
