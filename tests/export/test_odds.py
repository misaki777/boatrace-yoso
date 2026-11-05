"""E-7 / E-8: 締切前オッズ(odds_snapshot・ev_rank)、公開とローカルの分け方、--odds-only。"""

import copy
import gzip
import json
from datetime import date

import pytest

from src.export import config
from src.export import export_day as ed
from src.export.build import (
    apply_odds, build_prediction, ev_rank, expected_value, flatten_odds, iter_races, odds_snapshot_meta,
    strip_numeric_odds,
)
from src.export.official_odds_input import load_day_snapshots
from src.export.schema import check_forbidden, validate_day
from tests.export.test_build import P, _boats
from tests.export.test_export_day import D, GEN, YMD, find, inputs, make_tables

STRATEGY = {"name": "top_k", "bet_type": "trifecta", "k": 5, "short_label": "3連単 上位5点", "description": "x"}


def _nest(flat):
    out = {}
    for comb, v in flat.items():
        keys = comb.split("-")
        node = out
        for k in keys[:-1]:
            node = node.setdefault(k, {})
        node[keys[-1]] = v
    return out


def snapshot_for(p_win, factor=1.0, bet_types=("trifecta", "exacta", "win"), race_id="20261102-02-01",
                 closed="2026-11-02T10:00:00+09:00", fetched="2026-11-02T09:55:12+09:00", **extra):
    from src.export.probs import combination_probs
    probs = combination_probs(p_win, use_model=False)
    odds = {bt: _nest({c: round(0.75 / p * factor, 1) for c, p in probs[bt].items()}) for bt in bet_types}
    return {"race_id": race_id, "closed_at": closed, "fetched_at": fetched, "minutes_before_close": 4.8,
            "odds_time_label": "", "is_final_odds": False, "odds": odds, **extra}


def test_ev_rank_bounds():
    assert config.EV_RANK_BOUNDS == (("A", 1.20), ("B", 1.00), ("C", 0.80))
    assert [ev_rank(x) for x in (1.2, 1.1999, 1.0, 0.9999, 0.8, 0.7999, None)] == ["A", "B", "B", "C", "C", "D", None]
    assert expected_value(0.05, 24.0) == pytest.approx(1.2) and ev_rank(expected_value(0.05, 24.0)) == "A"
    assert expected_value(0.1, None) is None


def test_flatten_odds():
    flat = flatten_odds({"trifecta": {"1": {"2": {"3": 9.2, "4": None}}}, "exacta": {"1": {"2": 4.6}},
                         "quinella": {"1": {"2": 3.1}}, "win": {"1": 1.8, "2": "-"},
                         "place": {"1": {"lower_limit": 1.0, "upper_limit": 1.3}}})
    assert flat == {("trifecta", "1-2-3"): 9.2, ("exacta", "1-2"): 4.6, ("quinella", "1=2"): 3.1,
                    ("win", "1"): 1.8, ("place", "1"): 1.0}


def test_apply_odds_local_and_public():
    pr = build_prediction(_boats(), P, variant="pre", predicted_at="2026-11-02T07:30:00+09:00", strategy=STRATEGY)
    assert pr["odds_snapshot"] is None
    apply_odds(pr, snapshot_for(P, factor=1.5))
    assert pr["odds_snapshot"] == {"fetched_at": "2026-11-02T09:55:12+09:00", "minutes_before_close": 4.8,
                                   "pages": ["odds3t", "odds2tf", "oddstf"], "status": "ok"}
    it = pr["picks"]["trifecta"][0]
    assert it["ev"] == round(it["prob"] * it["odds"], 2) and it["ev_rank"] == ev_rank(it["prob"] * it["odds"])
    assert all("ev_rank" in x for x in pr["recommended"])
    pub = strip_numeric_odds({"stadiums": [{"races": [{"prediction": pr, "result": None}]}]})
    p2 = pub["stadiums"][0]["races"][0]["prediction"]
    assert "odds" not in p2["picks"]["win"][0] and "ev" not in p2["recommended"][0]
    assert "ev_rank" in p2["picks"]["win"][0]


def test_partial_and_status_from_input():
    pr = build_prediction(_boats(), P, variant="pre", predicted_at="2026-11-02T07:30:00+09:00", strategy=STRATEGY)
    apply_odds(pr, snapshot_for(P, bet_types=("trifecta",)))
    assert pr["odds_snapshot"]["status"] == "partial" and pr["odds_snapshot"]["pages"] == ["odds3t"]
    # 取れなかったページの券種は ev_rank=null(オッズが無い組合せと同じ扱い)
    assert pr["picks"]["trifecta"][0]["ev_rank"] is not None and pr["picks"]["win"][0]["ev_rank"] is None
    # 収集担当の形: status・pages(dict)・pages_requested
    snap = snapshot_for(P, bet_types=("trifecta", "exacta"), status="ok", pages_requested=["odds3t", "odds2tf"],
                        pages={"odds3t": {"ok": True}, "odds2tf": {"ok": True}})
    assert odds_snapshot_meta(snap)["pages"] == ["odds3t", "odds2tf"]
    assert odds_snapshot_meta(snap)["status"] == "ok"
    late = {"race_id": "x", "fetched_at": None, "status": "late", "odds": {}}
    apply_odds(pr, late)
    assert pr["odds_snapshot"]["status"] == "late" and "ev_rank" not in pr["picks"]["trifecta"][0]


def _inputs_with_snapshots():
    inp = inputs()
    preds = inp.predictions[inp.predictions["variant"] == "pre"]
    snaps = {}
    for rid, g in preds.groupby("race_id"):
        if rid.startswith(f"{YMD}-02-"):
            p = g.sort_values("entry_number")["p_win"].tolist()
            snaps[rid] = snapshot_for(p, factor=1.3, race_id=rid)
    inp.odds_snapshots = snaps
    return inp


def test_build_day_with_snapshots_public_has_no_numbers():
    day = ed.build_day(D, _inputs_with_snapshots(), generated_at=GEN)
    assert validate_day(day, local=True) == []
    pub = strip_numeric_odds(day)
    assert validate_day(pub) == [] and check_forbidden(pub) == []
    text = json.dumps(pub)
    assert '"odds"' not in text and '"ev"' not in text and '"ev_rank"' in text
    r = find(day, 2, 1)
    assert r["prediction"]["odds_snapshot"]["status"] == "ok"
    assert find(day, 12, 1)["prediction"]["odds_snapshot"] is None


def test_publish_numeric_switch(tmp_path, monkeypatch):
    inp = _inputs_with_snapshots()
    out = tmp_path / "docs"
    ed.export_day(D, out, inputs=inp, generated_at=GEN)
    pub = json.loads((out / "days" / f"{YMD}.json").read_text())
    loc = json.loads((tmp_path / "docs-local" / "days" / f"{YMD}.json").read_text())
    assert '"odds"' not in json.dumps(pub) and '"odds"' in json.dumps(loc)
    assert loc == json.loads(json.dumps(ed.build_day(D, inp, generated_at=GEN)))
    monkeypatch.setattr(config, "PUBLISH_ODDS_NUMERIC", True)
    ed.export_day(D, out, inputs=inp, generated_at=GEN)
    assert json.loads((out / "days" / f"{YMD}.json").read_text()) == loc


def test_odds_only_matches_full_export(tmp_path):
    """--odds-only(前回のローカルJSONにオッズだけ反映)は、通常の export と同じ結果になる。"""
    out = tmp_path / "docs"
    ed.export_day(D, out, inputs=inputs(), generated_at=GEN)           # まだオッズなし
    snaps = _inputs_with_snapshots().odds_snapshots
    ed.export_odds_only(D, out, snapshots=snaps, generated_at=GEN)
    fast_pub = (out / "days" / f"{YMD}.json").read_text()
    fast_loc = (tmp_path / "docs-local" / "days" / f"{YMD}.json").read_text()
    full = tmp_path / "full"
    inp = inputs()
    inp.odds_snapshots = snaps
    ed.export_day(D, full, inputs=inp, generated_at=GEN)
    assert fast_pub == (full / "days" / f"{YMD}.json").read_text()
    assert fast_loc == (tmp_path / "full-local" / "days" / f"{YMD}.json").read_text()


def test_odds_only_cli_falls_back_without_local(tmp_path, monkeypatch):
    called = {}
    monkeypatch.setattr(ed, "export_day", lambda *a, **k: called.setdefault("full", True) or tmp_path)
    ed.export_odds_only(D, tmp_path / "docs", snapshots={}, generated_at=GEN)
    assert called == {"full": True}


def _write_snap(base, rid, timing, obj):
    d = base / "2026" / YMD
    d.mkdir(parents=True, exist_ok=True)
    ss_rr = rid.split("-", 1)[1]
    with gzip.open(d / f"{ss_rr}_{timing}.json.gz", "wt", encoding="utf-8") as f:
        json.dump(obj, f)
    (d / f"{ss_rr}_odds3t_{timing}.html.gz").write_bytes(b"")


def test_load_day_snapshots_from_files(tmp_path):
    rid = f"{YMD}-02-01"
    _write_snap(tmp_path, rid, "T10", snapshot_for(P, race_id=rid, fetched="2026-11-02T09:50:00+09:00"))
    _write_snap(tmp_path, rid, "T05", snapshot_for(P, race_id=rid, fetched="2026-11-02T09:55:00+09:00"))
    # 締切後に取ったもの(締切時オッズ)は使わない
    _write_snap(tmp_path, f"{YMD}-02-02", "TC", snapshot_for(P, race_id=f"{YMD}-02-02", is_final_odds=True,
                                                             minutes_before_close=-5.0))
    snaps = load_day_snapshots(D, tmp_path)
    assert list(snaps) == [rid] and snaps[rid]["fetched_at"] == "2026-11-02T09:55:00+09:00"


def test_stopped_day(tmp_path):
    d = tmp_path / "2026" / YMD
    d.mkdir(parents=True)
    (d / "_stopped.json").write_text(json.dumps({"stopped_at": "2026-11-02T12:00:00+09:00", "reason": "http_403"}))
    inp = inputs()
    from src.export.official_odds_input import load_stopped
    inp.odds_stopped = load_stopped(D, tmp_path)
    day = ed.build_day(D, inp, generated_at=GEN)
    assert find(day, 2, 1)["prediction"]["odds_snapshot"] is None        # 10:00 締切(止める前)
    assert find(day, 2, 12)["prediction"]["odds_snapshot"]["status"] == "stopped"   # 15:30 締切
    assert validate_day(day, local=True) == []


def test_schema_rejects_ev_rank_without_snapshot():
    day = ed.build_day(D, inputs(), generated_at=GEN)
    bad = copy.deepcopy(day)
    find(bad, 2, 1)["prediction"]["picks"]["win"][0]["ev_rank"] = "A"
    assert any("ev_rank" in e for e in validate_day(bad))
    bad2 = copy.deepcopy(day)
    find(bad2, 2, 1)["prediction"]["odds_snapshot"] = {"fetched_at": None, "minutes_before_close": None,
                                                       "pages": [], "status": "unknown"}
    assert validate_day(bad2)
