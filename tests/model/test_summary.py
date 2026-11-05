"""M-11: E が import する集計関数のテスト(lightgbm を使わない)。"""

import numpy as np
import pandas as pd
import pytest

from src.model import summary as sm
from src.model.feature_labels import FEATURE_LABELS, is_value_public, label_for, unlabeled


def test_wilson():
    lo, hi = sm.wilson_ci(50, 100)
    assert lo == pytest.approx(0.4038, abs=1e-3) and hi == pytest.approx(0.5962, abs=1e-3)
    assert sm.wilson_ci(0, 10)[0] == 0.0 and sm.wilson_ci(10, 10)[1] == 1.0
    assert sm.wilson_ci(0, 0) is None


def test_calibration_bins():
    p = np.array([0.05, 0.08, 0.55, 0.58, 0.95, 1.0])
    y = np.array([0, 0, 1, 0, 1, 1])
    bins = sm.calibration_bins(p, y)
    assert [b["bin"] for b in bins] == ["0-10", "50-60", "90-100"]  # 1.0 は最後の帯
    b = bins[1]
    assert b["n"] == 2 and b["actual"] == 0.5 and b["pred_mean"] == pytest.approx(0.565)
    assert b["ci95"][0] < 0.5 < b["ci95"][1]


def test_streak_drawdown_note():
    assert sm.max_losing_streak([True, False, False, True, False, False, False]) == 3
    assert sm.max_losing_streak([]) == 0
    assert sm.max_drawdown_yen([100, -300, 50, -100, 500]) == 350
    assert sm.sample_note(199) == "few" and sm.sample_note(200) is None


def _preds(n_races=30):
    rows = []
    for r in range(n_races):
        rid = f"202611{1 + r // 12:02d}-01-{r % 12 + 1:02d}"
        for e, p in enumerate([0.5, 0.2, 0.1, 0.1, 0.05, 0.05], start=1):
            rows.append({"race_id": rid, "entry_number": e, "p_win": p, "is_valid_result": True,
                         "is_absent": False, "finish_position": e if r % 3 else (2 if e == 1 else (1 if e == 2 else e))})
    return pd.DataFrame(rows)


def _payouts(preds):
    rows = []
    for rid, g in preds.groupby("race_id"):
        order = g.sort_values("finish_position")["entry_number"].tolist()
        rows += [(rid, "win", str(order[0]), 200, None),
                 (rid, "trifecta", "-".join(map(str, order[:3])), 1000, None),
                 (rid, "exacta", "-".join(map(str, order[:2])), 500, None)]
    return pd.DataFrame(rows, columns=["race_id", "bet_type", "combination", "amount", "label"])


def test_compare_strategies_shape():
    preds = _preds()
    rows = sm.compare_strategies(preds, _payouts(preds), n_boot=100)
    names = [r["name"] for r in rows]
    assert "baseline_win1" in names and "baseline_tri1_top5" in names
    keys = {"name", "short_label", "races", "bets", "roi", "roi_ci95", "hit_rate", "max_losing_streak",
            "max_drawdown_yen", "is_baseline", "leak_free", "sample_note"}
    for r in rows:
        assert keys <= set(r)
        assert r["races"] == 30 and r["sample_note"] == "few"
    win1 = next(r for r in rows if r["name"] == "baseline_win1")
    # 1号艇は3レースに1回負ける(2レース勝って1レース負けるの繰り返し)
    assert win1["bets"] == 30 and win1["roi"] == pytest.approx(200 * 20 / 3000)
    assert win1["max_losing_streak"] == 1 and win1["is_baseline"] is True
    tri = next(r for r in rows if r["name"] == "baseline_tri1_top5")
    assert tri["bets"] == 150


def test_baseline_tri1_only_starts_with_1():
    preds = _preds(3)
    spec = next(s for s in sm.DEFAULT_STRATEGIES if s["name"] == "baseline_tri1_top5")
    settled, n = sm.settle_strategy(preds, _payouts(preds), spec)
    assert n == 3 and settled["combination"].str.startswith("1-").all()


def test_calibration_from_predictions():
    cal = sm.calibration_from_predictions(_preds())
    assert sum(b["n"] for b in cal) == 180
    assert all("ci95" in b for b in cal)


def test_by_month():
    preds = _preds()
    settled, _ = sm.settle_strategy(preds, _payouts(preds), sm.DEFAULT_STRATEGIES[0])
    m = sm.by_month(settled, n_boot=50)
    assert [x["month"] for x in m] == ["2026-11"] and m[0]["races"] == 30


def test_feature_labels_cover_all_features():
    from src.features.feature_sets import features_for

    for v in ("pre", "preview", "pre_v2", "preview_v2"):
        assert unlabeled(features_for(v)) == []
    assert label_for("national_win_rate") == "全国勝率"
    assert label_for("unknown_feature") == "unknown_feature"
    assert not is_value_public("age") and not is_value_public("weight") and is_value_public("national_win_rate")
    assert len(FEATURE_LABELS) == len(set(FEATURE_LABELS.values()))  # ラベルの重複なし
