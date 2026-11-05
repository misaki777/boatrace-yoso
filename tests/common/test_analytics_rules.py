"""src/common/analytics_rules.py のテスト。"""

from datetime import date, datetime

import pytest

from src.common import analytics_rules as ar


@pytest.mark.parametrize("n,level", [(None, "insufficient"), (0, "insufficient"), (29, "insufficient"),
                                     (30, "few"), (199, "few"), (200, "normal"), (5000, "normal")])
def test_sample_level(n, level):
    assert ar.sample_level(n) == level


def test_rate_or_none():
    assert ar.rate_or_none(10, 29) is None
    assert ar.rate_or_none(10, 40) == 0.25
    assert ar.rate_or_none(1, 300, ndigits=3) == 0.003


def test_wilson_known_values():
    lo, hi = ar.wilson_interval(50, 100)
    assert lo == pytest.approx(0.4038, abs=1e-4)
    assert hi == pytest.approx(0.5962, abs=1e-4)
    lo, hi = ar.wilson_interval(0, 10)
    assert lo == 0.0 and hi == pytest.approx(0.2775, abs=1e-4)
    assert ar.wilson_interval(0, 0) is None
    with pytest.raises(ValueError):
        ar.wilson_interval(5, 3)


def test_bootstrap_deterministic_and_contains_point():
    stakes = [500] * 100
    returns = [0] * 80 + [2500] * 20  # 回収率 1.0
    a = ar.bootstrap_roi_interval(stakes, returns)
    b = ar.bootstrap_roi_interval(stakes, returns)
    assert a == b
    assert a[0] < 1.0 < a[1]
    assert ar.ci_crosses(a)
    assert ar.bootstrap_roi_interval([], []) is None
    assert ar.bootstrap_roi_interval([0, 0], [0, 0]) is None
    with pytest.raises(ValueError):
        ar.bootstrap_roi_interval([1], [1, 2])
    assert ar.BOOTSTRAP_ITERATIONS == 200


def test_ci_crosses():
    assert not ar.ci_crosses((0.6, 0.9))
    assert ar.ci_crosses((0.9, 1.1))
    assert not ar.ci_crosses(None)


@pytest.mark.parametrize("m,s", [(3, "spring"), (5, "spring"), (6, "summer"), (8, "summer"),
                                 (9, "autumn"), (11, "autumn"), (12, "winter"), (1, "winter"), (2, "winter")])
def test_season(m, s):
    assert ar.season_of(date(2026, m, 1)) == s


def test_night_and_time_band():
    assert ar.is_night(datetime(2026, 11, 5, 16, 59)) is False
    assert ar.is_night(datetime(2026, 11, 5, 17, 0)) is True
    assert ar.is_night(None) is None
    assert ar.time_band(datetime(2026, 11, 5, 11, 59)) == "morning"
    assert ar.time_band(datetime(2026, 11, 5, 12, 0)) == "day"
    assert ar.time_band(datetime(2026, 11, 5, 20, 0)) == "night"


def test_wind():
    assert ar.wind_speed_band(0) == "weak"
    assert ar.wind_speed_band(2) == "weak"
    assert ar.wind_speed_band(3) == "mid"
    assert ar.wind_speed_band(6) == "strong"
    assert ar.wind_bucket(17, 0) == "none"
    assert ar.wind_bucket(5, 0) == "none"
    assert ar.wind_bucket(5, 4) == "tail_mid"
    assert ar.wind_bucket(13, 7) == "head_strong"
    assert ar.wind_bucket(1, 1) == "cross_weak"
    assert ar.wind_bucket(None, 3) is None
    assert len(ar.WIND_BUCKETS) == 10


def test_grade_bucket():
    assert [ar.grade_bucket(g) for g in (1, 6, 2, 3, 4, 5)] == ["top", "top", "top", "g2g3", "g2g3", "general"]
    assert ar.grade_bucket(None) is None


def test_prob_bands():
    assert ar.prob_band10(0.0) == "0-10"
    assert ar.prob_band10(0.55) == "50-60"
    assert ar.prob_band10(1.0) == "90-100"
    with pytest.raises(ValueError):
        ar.prob_band10(1.2)
    assert ar.prob_band3(0.349) == "lt35"
    assert ar.prob_band3(0.35) == "35to50"
    assert ar.prob_band3(0.5) == "ge50"


def test_manshu():
    assert ar.is_manshu(10000) is True
    assert ar.is_manshu(9990) is False
    assert ar.is_manshu(None) is None


def test_size_limits():
    ar.check_size("weekly", 20 * 1024)
    with pytest.raises(ar.SizeLimitExceeded):
        ar.check_size("weekly", 20 * 1024 + 1)
    with pytest.raises(KeyError):
        ar.check_size("unknown", 1)
    assert ar.SIM_BUDGET_PER_RACE_YEN == 1000
