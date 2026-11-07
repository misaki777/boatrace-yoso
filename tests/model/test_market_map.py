"""回収率の地図: 帯の作り方、前半で見つけたものを後半で確かめる仕組み、公開用の表にオッズが入らないこと。"""

import numpy as np
import pandas as pd

from src.model import market_map as mm


def test_bands():
    assert list(mm.odds_band(np.array([1.5, 2.0, 45.0, 1500.0]))) == ["1〜2倍", "2〜5倍", "20〜50倍", "1000倍〜"]
    assert list(mm.pop_band(np.array([1, 3, 6, 50]))) == ["1番人気", "2〜3番人気", "4〜6番人気", "41〜120番人気"]


def test_roi_ci_contains_point():
    rng = np.random.default_rng(0)
    ret = rng.choice([0, 500], size=2000, p=[0.85, 0.15]).astype(float)
    ci = mm.roi_ci(np.full(2000, 100.0), ret)
    assert ci[0] < ret.sum() / 200000 < ci[1]


def _frame(n_races, roi_good, start):
    rows = []
    for i in range(n_races):
        rid = f"r{start}{i:05d}"
        d = pd.Timestamp(start)
        hit_good = (i % 10) == 0
        rows.append({"race_id": rid, "date": d, "bet_type": "win", "pop_band": "2〜3番人気", "boat": 1, "stadium": 1,
                     "time": "昼", "grade": "一般", "season": "春", "preview": "直前情報あり",
                     "ret": 1000.0 * roi_good if hit_good else 0.0})
        rows.append({"race_id": rid, "date": d, "bet_type": "win", "pop_band": "2〜3番人気", "boat": 2, "stadium": 1,
                     "time": "昼", "grade": "一般", "season": "春", "preview": "直前情報あり",
                     "ret": 500.0 if (i % 10) == 1 else 0.0})
    return pd.DataFrame(rows)


def test_discover_needs_second_half():
    first = _frame(400, 1.5, "2026-03-01")
    good = mm.discover(first, _frame(400, 1.5, "2026-08-01"), top_n=5)
    bad = mm.discover(first, _frame(400, 0.5, "2026-08-01"), top_n=5)
    g = {c["value"]: c for c in good["candidates"] if c["dimension"] == "boat"}
    b = {c["value"]: c for c in bad["candidates"] if c["dimension"] == "boat"}
    assert g[1]["replicated"] and not b[1]["replicated"]  # 後半で再現しなければ候補にしない
    assert good["tested_cells"] > 0 and g[1]["first"]["roi_ci_bonferroni"] is not None


def test_public_table_has_no_odds():
    import json
    from pathlib import Path

    p = Path("reports/market_map_public.json")
    if p.exists():
        text = p.read_text(encoding="utf-8")
        assert "odds" not in text and "倍" not in text
        assert "popularity" in json.loads(text)["by_popularity"][0]
