"""E-3: export_index(index.json / performance.json)を合成データで確かめる。"""

import json

import pytest

from src.export import export_index as ei
from src.export import make_sample
from src.export.io_util import read_json
from src.export.schema import validate_index, validate_performance

GEN = "2026-11-03T09:00:00+09:00"


def _run(strategy, bet_type, params, leak_free, **kw):
    base = {"strategy": strategy, "bet_type": bet_type, "params": params, "leak_free": leak_free,
            "races": 100, "bet_races": 80, "bets": 400, "hits": 30, "stake": 40000, "return": 33000,
            "roi": 0.825, "hit_rate": 0.3, "bet_hit_rate": 0.075, "roi_ci95": [0.7123, 0.9555]}
    base.update(kw)
    return base


@pytest.fixture
def env(tmp_path):
    out = tmp_path / "docs"
    make_sample.make_all(out)          # days/ に3日分
    (out / "index.json").unlink()
    (out / "performance.json").unlink()
    models = tmp_path / "models"
    models.mkdir()
    (models / "latest").write_text("pre_x_1\n", encoding="utf-8")
    reports = tmp_path / "reports"
    for name, runs in {
        "top_k": [_run("top_k", "trifecta", {"k": 5}, True)],
        "ev_final": [_run("ev_final", "trifecta", {"tau": 1.2}, False, roi=1.051)],
        "prob_threshold": [_run("prob_threshold", "trifecta", {"theta": 0.3}, True, bet_races=0, bets=0,
                                stake=0, roi=0.0, hit_rate=None, roi_ci95=[0.0, 0.0])],
    }.items():
        d = reports / "backtest" / "pre_x_1" / f"{name}_test"
        d.mkdir(parents=True)
        (d / "summary.json").write_text(json.dumps({"model_id": "pre_x_1", "period": "test", "strategy": name,
                                                    "runs": runs}), encoding="utf-8")
    ev = reports / "eval" / "pre_x_1"
    ev.mkdir(parents=True)
    (ev / "metrics.json").write_text(json.dumps({
        "periods": {"test_from": "2026-10-01", "test_to": "2026-10-31"},
        "results": {"test": {"B0": {"win_logloss": 9.9},
                             "LightGBM(pre_x_1)": {"win_logloss": 1.20581, "top1_acc": 0.5571,
                                                   "trifecta_top10_hit": 0.485, "calibration": []}}}}),
        encoding="utf-8")
    return out, models, reports


def test_export_index_and_performance(env):
    out, models, reports = env
    ei.export_index(out, GEN, models_dir=models, reports_dir=reports)
    idx = read_json(out / "index.json")
    perf = read_json(out / "performance.json")
    assert validate_index(idx) == [] and validate_performance(perf) == []
    assert [d["date"] for d in idx["dates"]] == ["2026-11-02", "2026-11-01", "2026-10-31"]
    bt = perf["backtest"]
    assert bt["model_id"] == "pre_x_1" and bt["period"] == {"from": "2026-10-01", "to": "2026-10-31"}
    assert bt["metrics"] == {"win_logloss": 1.2058, "top1_acc": 0.5571, "trifecta_top10_hit": 0.485}
    names = [s["name"] for s in bt["strategies"]]
    assert names == ["top_k", "prob_threshold", "ev_final"]       # STRATEGY_ORDER の順
    top = {k: v for k, v in bt["strategies"][0].items()
           if k not in ("max_losing_streak", "max_drawdown_yen", "is_baseline")}
    assert bt["strategies"][0]["is_baseline"] is False
    assert top == {"name": "top_k", "params": {"bet_type": "trifecta", "k": 5}, "roi": 0.825,
                   "roi_ci95": [0.712, 0.956], "hit_rate": 0.3, "leak_free": True, "races": 80, "bets": 400,
                   "short_label": "3連単 上位5点"}
    zero = bt["strategies"][1]
    assert zero["roi"] is None and zero["hit_rate"] is None and zero["roi_ci95"] is None
    assert bt["strategies"][2]["leak_free"] is False
    assert perf["live"]["strategy_label"] == "3連単 上位5点・荒れ見送り"


def test_live_matches_days(env):
    out, models, reports = env
    ei.export_index(out, GEN, models_dir=models, reports_dir=reports)
    perf = read_json(out / "performance.json")
    days = ei.load_days(out)
    stake = sum(d["day_summary"]["stake"] for d in days)
    ret = sum(d["day_summary"]["return"] for d in days)
    assert perf["live"]["total"]["stake"] == stake and perf["live"]["total"]["return"] == ret
    assert perf["live"]["by_day"][-1]["cum_roi"] == round(ret / stake, 3)


def test_no_reports_gives_null_backtest(env, tmp_path):
    out, _, _ = env
    ei.export_index(out, GEN, models_dir=tmp_path / "none", reports_dir=tmp_path / "none")
    assert read_json(out / "performance.json")["backtest"] is None


def test_idempotent(env):
    out, models, reports = env
    ei.export_index(out, GEN, models_dir=models, reports_dir=reports)
    a = (out / "index.json").read_text(), (out / "performance.json").read_text()
    ei.export_index(out, GEN, models_dir=models, reports_dir=reports)
    assert a == ((out / "index.json").read_text(), (out / "performance.json").read_text())
