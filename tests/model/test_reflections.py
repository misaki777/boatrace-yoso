"""反省点: 両方の期間で上がる候補だけ、件数不足は出さない、増額系・禁止語の文は出ない。"""

import numpy as np
import pandas as pd
import pytest

from src.model import reflections as rf


def _res(rois_by_cat: dict, n: int, cat_of) -> pd.DataFrame:
    rows = []
    for i in range(n):
        c = cat_of(i)
        rows.append({"race_id": f"r{i:04d}", "stake": 1000.0, "return": 1000.0 * rois_by_cat[c], "hit": rois_by_cat[c] > 0,
                     "bought": True})
    return pd.DataFrame(rows)


def _attrs(n, cat_of):
    return pd.DataFrame({"conf": [cat_of(i) for i in range(n)]}, index=[f"r{i:04d}" for i in range(n)])


def test_only_both_periods_qualify():
    cat = lambda i: "「荒」" if i % 4 == 0 else "「堅」"  # noqa: E731
    # 過去: 荒が悪い / 実運用: 荒が悪い → 候補になる
    hist = _res({"「荒」": 0.4, "「堅」": 0.9}, 800, cat)
    live = _res({"「荒」": 0.5, "「堅」": 0.85}, 1000, cat)
    c = rf._qualify(rf._skip_candidates({"history": hist, "live": live}, {"history": _attrs(800, cat), "live": _attrs(1000, cat)}))
    assert [x["action"] for x in c] == ["「荒」のレースを買わない"] and c[0]["level"] == "normal"
    # 実運用では荒の方が良い → 片方だけの改善は出さない
    live2 = _res({"「荒」": 1.5, "「堅」": 0.85}, 400, cat)
    c2 = rf._qualify(rf._skip_candidates({"history": hist, "live": live2}, {"history": _attrs(800, cat), "live": _attrs(400, cat)}))
    assert c2 == []
    # 外すレースが30件未満(件数不足)なら出さない
    live3 = _res({"「荒」": 0.5, "「堅」": 0.85}, 80, cat)
    c3 = rf._qualify(rf._skip_candidates({"history": hist, "live": live3}, {"history": _attrs(800, cat), "live": _attrs(80, cat)}))
    assert c3 == []


def test_texts_have_no_forbidden_words():
    c = {"action": "2点に絞る", "nums": {"history": {"before": 0.83, "after": 0.86}, "live": {"before": 0.82, "after": 0.85}},
         "level": "normal"}
    t = rf.candidate_text(c)
    assert t == "2点に絞ると、過去 83%→86%、実運用 82%→85%。"
    for bad in ("賭け金を増やすと", "取り返す", "最適な買い方", "必ず当たる"):
        with pytest.raises(ValueError):
            rf.check_text(bad)
    # 候補の種類に、金額を増やすものが無い
    import inspect

    src = inspect.getsource(rf)
    assert "budget *" not in src and "* budget" not in src


def test_loss_text_needs_overrepresentation():
    rows = [{"dimension": "conf", "category": "「荒」", "level": "normal", "loss_share": 0.5, "stake_share": 0.2, "profit": -100},
            {"dimension": "conf", "category": "「堅」", "level": "normal", "loss_share": 0.5, "stake_share": 0.8, "profit": -100}]
    assert rf.loss_text(rows) == "負けの約5割が「荒」のレースから(買ったレースでは約2割)。"
    assert rf._dim_rows("conf", pd.DataFrame({"races": [10], "stake": [1.0], "ret": [0.5], "profit": [-0.5]},
                                             index=["「堅」"])) == []  # 区分が1つなら内訳にしない
