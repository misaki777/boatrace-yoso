"""M-15・M-17: 実運用の確定版だけを使う集計のテスト(lightgbm を使わない)。"""

import numpy as np
import pandas as pd
import pytest

from src.model import analytics_eval as ae


def _interim(n_races, day="2026-11-02"):
    rid = [f"{day.replace('-', '')}-01-{r + 1:02d}" for r in range(n_races)]
    ent = pd.DataFrame([{"race_id": r, "entry_number": e, "finish_position": e if i % 3 else (2 if e == 1 else (1 if e == 2 else e)),
                         "is_absent": False} for i, r in enumerate(rid) for e in range(1, 7)])
    races = pd.DataFrame({"race_id": rid, "date": pd.Timestamp(day), "is_valid_result": True, "stadium_number": 1,
                          "technique_number": [2 if i % 3 == 0 else 1 for i in range(n_races)],
                          "pv_weather_number": 1, "pv_wind_direction_number": 17, "pv_wind_speed": 0.0})
    pay = pd.DataFrame([(r, "win", "1" if i % 3 else "2", 200, None) for i, r in enumerate(rid)],
                       columns=["race_id", "bet_type", "combination", "amount", "label"])
    return rid, {"entries": ent, "races": races, "payouts": pay}


@pytest.fixture
def live_env(tmp_path, monkeypatch):
    rid, tables = _interim(60)
    import src.features.build_interim as bi

    monkeypatch.setattr(bi, "read_interim", lambda t, *a, **k: tables[t].copy())
    p = [0.62, 0.15, 0.1, 0.07, 0.04, 0.02]
    pred = pd.DataFrame([{"race_id": r, "entry_number": e, "model_id": "pre_x", "p_win": p[e - 1]}
                         for r in rid for e in range(1, 7)])
    pred.to_parquet(tmp_path / "20261102_pre.parquet")
    # バックテスト用の予想など、確定版のファイル名でないものは読まない
    pred.assign(p_win=1 / 6).to_parquet(tmp_path / "predictions_test.parquet")
    return tmp_path


def test_rate_cell_levels():
    assert ae.rate_cell(5, 20)["actual"] is None and ae.rate_cell(5, 20)["level"] == "insufficient"
    c = ae.rate_cell(20, 40, 24.0)
    assert c["level"] == "few" and c["actual"] == 0.5 and c["pred_mean"] == 0.6 and c["diff"] == pytest.approx(0.1)
    assert ae.rate_cell(150, 300)["level"] == "normal"


def test_uses_only_locked_live_predictions(live_env):
    live = ae.load_live("2026-11-01", "2026-11-03", live_env)
    assert set(live["version"]) == {"pre"} and (live["model_id"] == "pre_x").all()
    assert len(live) == 360 and live["p_win"].max() == pytest.approx(0.62)  # predictions_test は読まない
    fav = ae.favorite_table(live)
    assert len(fav) == 60 and (fav["fav_entry"] == 1).all()
    assert fav["fav_won"].mean() == pytest.approx(40 / 60)
    assert set(fav["prob_band"]) == {"60-70"} and set(fav["band3"]) == {"ge50"}


def test_model_weakness(live_env, tmp_path_factory):
    w = ae.build_model_weakness("2026-11-01", "2026-11-03", live_env, tmp_path_factory.mktemp("m"))
    assert w["source"] == "live" and w["races"] == {"pre": 60, "preview": 0}
    oc = w["overconfidence_weekly"][0]
    assert oc["n"] == 60 and oc["pred_mean"] == pytest.approx(0.62) and oc["actual"] == pytest.approx(0.6667, abs=1e-4)
    tech = {r["key"]: r for r in w["conditions"]["pre"]["technique"]}
    assert tech[2]["level"] == "insufficient" and tech[2]["actual"] is None  # 20件は率を出さない
    assert tech[1]["actual"] == 1.0
    assert w["conditions"]["pre"]["wind"][0]["key"] == "none"
    assert w["calibration_weekly"][0]["races"] == 60


def test_weekly(live_env):
    wk = ae.build_weekly("2026-W45", live_env)
    assert (wk["from"], wk["to"]) == ("2026-11-02", "2026-11-08")
    ge50 = next(b for b in wk["favorite"]["pre"] if b["band"] == "ge50")
    assert ge50["n"] == 60
    names = {r["name"]: r for r in wk["strategies"]["pre"]}
    assert names["baseline_win1"]["roi"] == pytest.approx(200 * 40 / 6000, abs=1e-4)
    assert names["baseline_win1"]["roi_ci95"] is not None and names["baseline_win1"]["level"] == "few"
    assert wk["vs_previous_week"]["previous_week"] == "2026-W44"
    assert wk["vs_previous_week"]["pre"]["fav_win_rate_diff"] is None  # 前の週は件数なし


def test_iso_week_range():
    assert ae.iso_week_range("2026-W01")[0] == pd.Timestamp("2025-12-29")
