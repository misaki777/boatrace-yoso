"""tests/model 共通のフィクスチャ。"""

import warnings

import pytest

from src.model.config import Periods
from src.model.synthetic import make_synthetic_features, make_synthetic_market

# 合成データ用の短い期間(テストを速くするため)
SMALL_PERIODS = Periods(
    warmup_from="2026-01-01",
    train_from="2026-01-01",
    train_to="2026-03-31",
    valid_from="2026-04-01",
    valid_to="2026-04-30",
    test_from="2026-05-01",
    test_to="2026-05-31",
)


@pytest.fixture(scope="session")
def small_periods():
    return SMALL_PERIODS


@pytest.fixture(scope="session")
def synthetic_features():
    return make_synthetic_features("2026-01-01", "2026-05-31", races_per_day=12, seed=1)


@pytest.fixture(scope="session")
def synthetic_market(synthetic_features):
    return make_synthetic_market(synthetic_features, seed=1)


@pytest.fixture(autouse=True)
def _ignore_fallback_warning():
    # feature_sets.py が出来るまでは仮の特徴量リストの警告が出る
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="src.features.feature_sets")
        yield
