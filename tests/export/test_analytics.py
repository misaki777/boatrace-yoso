"""E-15: 分析用の公開JSON(spec 11)。"""

import gzip
import json
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from src.common import analytics_rules as ar
from src.export import analytics as an
from src.export.schema import SchemaError, check_forbidden

REPO = Path(__file__).resolve().parents[2]
GEN = "2026-11-05T06:00:00+09:00"


def test_records_hide_rates_below_30():
    df = pd.DataFrame([{"course": 1, "n": 29, "win_rate": 0.5, "win_lo": 0.3, "top2_rate": 0.7},
                       {"course": 2, "n": 30, "win_rate": 0.1234567, "win_lo": 0.05, "top2_rate": 0.3},
                       {"course": 3, "n": 250, "win_rate": 0.1, "win_lo": 0.08, "top2_rate": 0.3}])
    rows = an.records(df, "n")
    assert rows[0] == {"course": 1, "n": 29, "win_rate": None, "win_lo": None, "top2_rate": None,
                       "level": "insufficient"}
    assert rows[1]["win_rate"] == 0.1235 and rows[1]["level"] == "few" and rows[2]["level"] == "normal"


def test_rankings_drop_small_and_rename_motor():
    df = pd.DataFrame([
        {"ranking": "motor", "scope": 2, "rank": 1, "racer_number": None, "racer_name": None, "stadium_number": 2,
         "race_number": 3, "entry_number": 4, "motor_number": 11, "value": 0.5, "n": 3.0, "value2": 0.6, "n2": 12.0},
        {"ranking": "series", "scope": 2, "rank": 1, "racer_number": 4002, "racer_name": "b", "stadium_number": 2,
         "race_number": 3, "entry_number": 2, "motor_number": 9, "value": 1.5, "n": 2.0, "value2": None, "n2": None},
        {"ranking": "series", "scope": 2, "rank": 2, "racer_number": 4003, "racer_name": "c", "stadium_number": 2,
         "race_number": 4, "entry_number": 2, "motor_number": 8, "value": 1.0, "n": 1.0, "value2": None, "n2": None},
        {"ranking": "nige", "scope": 0, "rank": 1, "racer_number": 4001, "racer_name": "選手", "stadium_number": 2,
         "race_number": 1, "entry_number": 1, "motor_number": 5, "value": 0.8, "n": 12.0, "value2": None, "n2": None},
    ])
    obj = an.build_rankings(date(2026, 11, 5), df, GEN)
    # 逃げ率など: 30未満は出さない。今節: 2走以上。モーター: 下限なし
    assert sorted(obj["rankings"]) == ["motor", "series"]
    assert [r["racer_number"] for r in obj["rankings"]["series"]["2"]] == [4002]
    row = obj["rankings"]["motor"]["2"][0]
    assert row["motor"] == 11 and "motor_number" not in row and row["n2"] == 12
    assert check_forbidden(obj) == []


def test_size_limit_fails(tmp_path):
    big = {"schema_version": 1, "kind": "weekly", "rows": [{"i": i, "x": str(i) * 50} for i in range(20000)]}
    with pytest.raises(ar.SizeLimitExceeded):
        an.write_analytics(big, "weekly/2026-W01.json", "weekly", (tmp_path,))
    assert not (tmp_path / "analytics" / "weekly" / "2026-W01.json").exists()


@pytest.mark.parametrize("key", ["odds", "ev", "purchases", "marks", "memo", "budget"])
def test_forbidden_items_fail(tmp_path, key):
    with pytest.raises(SchemaError):
        an.write_analytics({"schema_version": 1, "rows": [{key: 1}]}, "x.json", "index", (tmp_path,))


def test_model_builders_wrapped_and_index(tmp_path):
    out = (tmp_path / "a", tmp_path / "b")
    an.export_model("2026-11-02", "2026-11-04", out, GEN,
                    builder=lambda f, t: {"source": "live", "period": {"from": f, "to": t}, "notes": ["説明"],
                                          "x": float("nan"), "y": 0.123456789})
    an.export_weekly("2026-W45", out, GEN, builder=lambda w: {"week": w, "from": "2026-11-02", "to": "2026-11-08"})
    an.export_simulator("2026-11-02", "2026-11-04", out, GEN,
                        builder=lambda f, t: {"validation": {"note": "後知恵を含む", "rows": []}, "live": {}})
    mw = json.loads((tmp_path / "a" / "analytics" / "model_weakness.json").read_text())
    assert mw["kind"] == "model_weakness" and mw["x"] is None and mw["y"] == 0.1235
    assert mw["explanations"] == ["説明"] and "notes" not in mw
    sim = json.loads((tmp_path / "b" / "analytics" / "simulator.json").read_text())
    assert sim["validation"]["explanation"] == "後知恵を含む"
    an.write_index(out, GEN)
    idx = json.loads((tmp_path / "a" / "analytics" / "index.json").read_text())
    assert idx["files"]["weekly"] == ["2026-W45"] and idx["files"]["model_weakness"]["period"]["to"] == "2026-11-04"


def test_rankings_keep_two_days(tmp_path):
    src = tmp_path / "in" / "rankings"
    src.mkdir(parents=True)
    df = pd.DataFrame([{"ranking": "avg_st", "scope": 0, "rank": 1, "racer_number": 1, "racer_name": "a",
                        "stadium_number": 1, "race_number": 1, "entry_number": 1, "motor_number": 1, "value": 0.15,
                        "n": 50.0, "value2": None, "n2": None}])
    for d in ("20261103", "20261104", "20261105"):
        df.to_parquet(src / f"{d}.parquet")
    for d in (date(2026, 11, 3), date(2026, 11, 4), date(2026, 11, 5)):
        an.export_rankings(d, (tmp_path / "o",), GEN, in_dir=tmp_path / "in")
    assert sorted(p.name for p in (tmp_path / "o" / "analytics" / "rankings").glob("*.json")) == [
        "20261104.json", "20261105.json"]


SAMPLE = REPO / "docs" / "data" / "sample" / "analytics"
LIMIT_KIND = {"stadiums.json": "stadiums", "model_weakness.json": "model_weakness", "simulator.json": "simulator",
              "index.json": "index", "personas.json": "personas"}


@pytest.mark.skipif(not SAMPLE.exists(), reason="サンプルが無い")
def test_sample_analytics_files():
    files = sorted(SAMPLE.rglob("*.json"))
    names = {str(p.relative_to(SAMPLE)) for p in files}
    for need in ("index.json", "stadiums.json", "model_weakness.json", "simulator.json", "cube/01.json"):
        assert need in names
    assert any(n.startswith("rankings/") for n in names) and any(n.startswith("weekly/") for n in names)
    total_cube = 0
    for p in files:
        obj = json.loads(p.read_text())
        assert check_forbidden(obj) == [], p
        size = len(gzip.compress(p.read_bytes()))
        rel = str(p.relative_to(SAMPLE))
        kind = LIMIT_KIND.get(rel) or {"cube": "cube_per_stadium", "rankings": "rankings",
                                       "weekly": "weekly"}[rel.split("/")[0]]
        ar.check_size(kind, size)
        if rel.startswith("cube/"):
            total_cube += size
            assert all(r["win_rate"] is None for r in obj["rows"] if r["n_starts"] < 30)
    ar.check_size("cube_total", total_cube)
    # シミュレーターは検証と実運用を分ける
    sim = json.loads((SAMPLE / "simulator.json").read_text())
    assert "validation" in sim and "live" in sim


def _persona_body(n_days=4, reflections=False):
    def row(i, races):
        r = {"id": f"p{i}", "name": f"さん{i}", "description": "x", "is_baseline": i == 1, "races_available": races,
             "races_bought": races, "stake": races * 1000, "return": races * 900, "profit": -races * 100,
             "level": ar.sample_level(races), "roi": 0.912345, "roi_ci95": [0.8, 1.0123456], "hit_rate": 0.31,
             "max_losing_streak": 5, "max_drawdown_yen": 1000,
             "by_day": [{"day": f"2026-11-{d + 1:02d}", "races_bought": 10, "stake": 10000 + d * 137 * i,
                         "return": 9000 + d * 7919 % 9973, "profit": -1000 - d * 31 * i, "cum_profit": -d * 977 * i,
                         "level": "insufficient", "roi": 0.9}
                        for d in range(n_days)],
             "by_week": [{"week": "2026-W45", "races_bought": races, "stake": 1, "return": 1, "profit": 0,
                          "cum_profit": 0, "roi": 1.0}]}
        if reflections:
            r["reflections"] = ["荒れたレースで外した"]
        return r
    return {"budget_per_race_yen": 1000, "rules": "固定",
            "live": {"from": "2026-11-02", "to": "2026-11-06", "app_strategy_changes": [],
                     "rows": [row(i, 12 if i == 2 else 300) for i in range(10)]},
            "history": {"from": "2026-10-01", "to": "2026-10-31", "note": "後知恵を含む",
                        "rows": [{k: v for k, v in row(i, 4000).items() if k != "by_day"} for i in range(10)]}}


def test_personas_levels_rounding_reflections(tmp_path):
    an.export_personas("2026-11-02", "2026-11-06", (tmp_path,), GEN,
                       builder=lambda f, t: _persona_body(reflections=True))
    obj = json.loads((tmp_path / "analytics" / "personas.json").read_text())
    rows = obj["live"]["rows"]
    assert len(rows) == 10 and rows[1]["is_baseline"] is True
    assert rows[0]["roi"] == 0.9123 and rows[0]["roi_ci95"] == [0.8, 1.0123]
    assert rows[2]["level"] == "insufficient" and rows[2]["roi"] is None and rows[2]["roi_ci95"] is None
    assert rows[2]["stake"] == 12000                                     # 件数・金額は残す
    assert rows[0]["by_day"][0]["roi"] is None                           # 日ごとの少数も率なし
    assert rows[0]["by_week"][0]["level"] == "normal"                    # level が無ければ件数から付ける
    assert rows[0]["reflections"] == ["荒れたレースで外した"]
    assert obj["history"]["explanation"] == "後知恵を含む" and obj["live"]["by_day_thinned"] is False
    assert check_forbidden(obj) == []


def test_personas_thins_daily_when_too_big(tmp_path):
    an.export_personas("2026-11-02", "2026-11-06", (tmp_path,), GEN, builder=lambda f, t: _persona_body(n_days=400))
    obj = json.loads((tmp_path / "analytics" / "personas.json").read_text())
    assert obj["live"]["by_day_thinned"] is True and all("by_day" not in r for r in obj["live"]["rows"])
    assert all(r["by_week"] for r in obj["live"]["rows"])


def test_personas_forbidden(tmp_path):
    body = _persona_body()
    body["live"]["rows"][0]["purchases"] = [{"race_id": "x"}]
    with pytest.raises(SchemaError):
        an.export_personas("a", "b", (tmp_path,), GEN, builder=lambda f, t: body)


@pytest.mark.skipif(not (SAMPLE / "personas.json").exists(), reason="サンプルが無い")
def test_sample_personas():
    p = SAMPLE / "personas.json"
    obj = json.loads(p.read_text())
    ar.check_size("personas", len(gzip.compress(p.read_bytes())))
    assert check_forbidden(obj) == []
    ids = [r["id"] for r in obj["live"]["rows"]]
    assert ids == ["win_fav", "boat1", "in_nige", "app", "box3", "quinella3", "steady", "upset", "outer_ana",
                   "select10"]
    assert any(r["is_baseline"] for r in obj["live"]["rows"]) and obj["history"]["rows"]
    assert "app_strategy_changes" in obj["live"]
