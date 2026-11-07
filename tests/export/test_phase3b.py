"""E-13 / E-14 / モデルの更新履歴(spec 10)。"""

import gzip
import json

import pandas as pd

from src.export import export_day as ed
from src.export import make_sample
from src.export.build import iter_races, strip_numeric_odds
from src.export.phase3b import build_models, build_racers, export_racers, round_to_one, tenkai_for_race
from src.export.schema import validate_day, validate_models, validate_racers
from tests.export.test_export_day import D, GEN, YMD, find, inputs

KEYS = ["nige", "sashi", "makuri", "makurisashi", "nuki", "megumare"]


def _tenkai_rows(race_ids, variant, base=0.55):
    rows = []
    for i, rid in enumerate(race_ids):
        rows.append({"race_id": rid, "p_nige": base + i * 0.001, "p_sashi": 0.16, "p_makuri": 0.14,
                     "p_makurisashi": 0.1, "p_nuki": 0.04, "p_megumare": 0.0101, "course1_entry": 1 if variant == "pre" else 2,
                     "model_id": f"tenkai_{variant}_x", "variant": variant,
                     "predicted_at": pd.Timestamp("2026-11-02 07:30", tz="Asia/Tokyo"),
                     "locked_at": pd.Timestamp("2026-11-02 07:30" if variant == "pre" else "2026-11-02 09:50",
                                               tz="Asia/Tokyo")})
    return pd.DataFrame(rows)


def test_round_to_one():
    r = round_to_one([0.5521, 0.1612, 0.1424, 0.0981, 0.0391, 0.0071])
    assert sum(r) == 1.0 or abs(sum(r) - 1.0) < 1e-12
    assert all(len(str(x).split(".")[-1]) <= 3 for x in r)


def test_tenkai_per_race_preview_then_pre():
    inp = inputs()
    rids = sorted(inp.races["race_id"])
    inp.tenkai = pd.concat([_tenkai_rows(rids, "pre"), _tenkai_rows([f"{YMD}-02-01"], "preview", 0.4)])
    day = ed.build_day(D, inp, generated_at=GEN)
    t1 = find(day, 2, 1)["prediction"]["tenkai"]
    t2 = find(day, 2, 2)["prediction"]["tenkai"]
    assert t1["version"] == "preview" and t1["course1_entry"] == 2 and t1["locked_at"] == "2026-11-02T09:50:00+09:00"
    assert t2["version"] == "pre" and t2["model_id"] == "tenkai_pre_x"
    for r in iter_races(day):
        t = (r["prediction"] or {}).get("tenkai")
        if t:
            assert abs(sum(t[k] for k in KEYS) - 1) < 1e-9
    assert validate_day(day, local=True) == []
    # ファイルが無い日は項目なし
    assert "tenkai" not in find(ed.build_day(D, inputs(), generated_at=GEN), 2, 1)["prediction"]
    # レースが欠けていれば null
    inp2 = inputs()
    inp2.tenkai = _tenkai_rows([f"{YMD}-02-01"], "pre")
    d2 = ed.build_day(D, inp2, generated_at=GEN)
    assert find(d2, 2, 3)["prediction"]["tenkai"] is None


def test_tenkai_invalid_sum_rejected():
    inp = inputs()
    inp.tenkai = _tenkai_rows(sorted(inp.races["race_id"]), "pre")
    day = ed.build_day(D, inp, generated_at=GEN)
    find(day, 2, 1)["prediction"]["tenkai"]["nige"] += 0.01
    assert validate_day(day, local=True)


def _cards():
    row = {"racer_number": 4001, "racer_name": "選手 1", "rank_number": 1, "branch_number": 13, "branch_name": None,
           "stadium_number": 2, "national_win_rate": 6.851, "national_top_2_percent": 50.12,
           "national_top_3_percent": 70.0, "local_win_rate": 6.0, "local_top_2_percent": 40.0,
           "local_top_3_percent": 60.0, "average_start_timing": 0.151, "flying_count": 1.0, "late_count": 0.0,
           "local365_n": 12, "local365_win_rate": 0.33333, "local365_top2_rate": 0.5, "local365_top3_rate": 0.75,
           "f_rate": 0.0123, "late_rate": 0.0, "maeduke_rate": 0.1,
           "age": 47, "weight": 52.0, "birthplace_number": 11, "motor_number": 21, "boat_number": 33}
    for k in range(1, 7):
        row.update({f"c{k}_n": 10, f"c{k}_win_rate": 0.2, f"c{k}_nige": 0.1, f"c{k}_sashi": 0.05,
                    f"c{k}_makuri": 0.03, f"c{k}_makurisashi": 0.02, f"c{k}_second": 0.2, f"c{k}_third": 0.2,
                    f"c{k}_st_mean": 0.16})
    res = [{"racer_number": 4001, "kind": "recent", "date": pd.Timestamp(f"2026-10-{10 + i}").date(),
            "stadium_number": 5, "race_number": 3, "course_number": 1, "place_number": 1 + i % 6,
            "finish_position": None, "technique_number": None} for i in range(12)]
    res.append({"racer_number": 4001, "kind": "series", "date": pd.Timestamp("2026-11-01").date(), "stadium_number": 2,
                "race_number": 7, "course_number": 2, "place_number": 1, "finish_position": 1, "technique_number": 2})
    return pd.DataFrame([row]), pd.DataFrame(res)


def test_racers_card():
    cards, res = _cards()
    obj = build_racers(D, cards, res, GEN)
    assert validate_racers(obj) == []
    r = obj["racers"]["4001"]
    assert r["rank"] == "A1" and r["branch"] == "東京" and r["nat"] == [6.85, 50.1, 70.0]
    assert r["l365"] == [12, 0.333, 0.5, 0.75] and len(r["course"]) == 6 and len(r["recent"]) == 10
    assert r["series"] == [["1101", 7, 2, 1, 2]]
    text = json.dumps(obj, ensure_ascii=False)
    for w in ('"age"', '"weight"', "birthplace", "motor_number", "boat_number", "47", "52.0"):
        assert w not in text, w
    bad = json.loads(text)
    bad["racers"]["4001"]["motor_number"] = 21
    assert validate_racers(bad)


def test_export_racers_keeps_two_days(tmp_path):
    cards, res = _cards()
    cdir = tmp_path / "cards"
    cdir.mkdir()
    out = tmp_path / "docs"
    (out / "racers").mkdir(parents=True)
    for old in ("20261030.json", "20261101.json"):
        (out / "racers" / old).write_text("{}")
    cards.to_parquet(cdir / f"{YMD}_cards.parquet")
    res.to_parquet(cdir / f"{YMD}_results.parquet")
    p = export_racers(D, (out,), cdir, GEN)
    assert sorted(x.name for x in (out / "racers").glob("*.json")) == ["20261101.json", f"{YMD}.json"]
    assert validate_racers(json.loads(p.read_text())) == []
    assert export_racers(D.replace(day=3), (out,), cdir, GEN) is None   # 入力が無い日


def test_sample_racers_size_and_fields(tmp_path):
    res = make_sample.make_all(tmp_path / "s", tmp_path / "sl")
    racers = res["racers"]
    assert validate_racers(racers) == []
    raw = (tmp_path / "s" / "racers" / "20261102.json").read_bytes()
    assert len(gzip.compress(raw)) < 200 * 1024
    # その日の出走選手が全員いる
    nums = {str(b["racer_number"]) for r in iter_races(res["days"]["2026-11-02"]) if r["prediction"]
            for b in r["prediction"]["boats"]}
    assert nums <= set(racers["racers"])
    assert '"age"' not in raw.decode() and "motor_number" not in raw.decode()
    # 展開予想は 11-02 だけ(過去日は項目なし)
    d = strip_numeric_odds(res["local_days"]["2026-11-02"])
    assert any((r["prediction"] or {}).get("tenkai") for r in iter_races(d))
    assert not any("tenkai" in (r["prediction"] or {}) for r in iter_races(res["days"]["2026-11-01"]))
    assert validate_models(res["models"]) == []


def test_models_history():
    hist = [{"date": "2026-12-02", "variant": "pre", "candidate": "pre_x", "adopted": True, "train_to": "2026-10-31",
             "valid": "2026-11", "metrics": {"win_logloss": {"candidate": 1.30123, "latest": 1.305, "path": "/x"}},
             "reason": "ok", "features": ["a"], "model_dir": "/home/x"}]
    obj = build_models(hist, GEN)
    assert validate_models(obj) == []
    h = obj["history"][0]
    assert set(h) == {"date", "variant", "candidate", "adopted", "train_to", "valid", "metrics", "reason"}
    assert h["metrics"] == {"win_logloss": {"candidate": 1.3012, "latest": 1.305}}
    assert build_models([], GEN)["history"] == []


def _detail_row(rid, variant, c1_entry=1):
    who = {"c1_nige": 0.5, "c2_sashi": 0.15, "c2_makuri": 0.005, "c3_makuri": 0.1, "c4_makuri": 0.12,
           "c4_makurisashi": 0.05, "c6_makurisashi": 0.009, "other": 0.066}
    entries = [c1_entry] + [e for e in range(1, 7) if e != c1_entry]
    return {"race_id": rid, "variant": variant, "model_id": f"td_{variant}", "headline": f"本線: {variant}",
            **{f"course{k}_entry": entries[k - 1] for k in range(1, 7)},
            **{f"p_who_{k}": v for k, v in who.items()},
            **{f"p_top_c{k}": [0.22, 0.15, 0.14, 0.24, 0.13, 0.12][k - 1] for k in range(1, 7)},
            **{f"p_faster_c{k}": 0.41 + k / 100 for k in range(2, 7)}}


def test_tenkai_detail():
    inp = inputs()
    rids = sorted(inp.races["race_id"])
    inp.tenkai = _tenkai_rows(rids, "pre")
    inp.tenkai_detail = pd.DataFrame([_detail_row(r, "pre") for r in rids] + [_detail_row(f"{YMD}-02-01", "preview", 2)])
    day = ed.build_day(D, inp, generated_at=GEN)
    t = find(day, 2, 1)["prediction"]["tenkai"]
    assert t["detail_version"] == "preview" and t["summary"] == {"text": "本線: preview"}
    assert t["by_boat"][0] == {"entry": 2, "course": 1, "nige": 0.5}            # 予定コース → 艇番
    assert t["by_boat"][1] == {"entry": 1, "course": 2, "sashi": 0.15}           # 0.01 未満は載せない
    assert all("other" not in b for b in t["by_boat"])
    assert t["start"]["top_start"]["2"] == 0.22 and abs(sum(t["start"]["top_start"].values()) - 1) < 1e-9
    assert t["start"]["kado_ahead_of_3"] == 0.45 and t["start"]["ahead_of_inner"]["2"] == 0.43
    assert find(day, 2, 2)["prediction"]["tenkai"]["detail_version"] == "pre"
    assert validate_day(day, local=True) == []
    # 詳しい版が無い日は項目なし
    inp.tenkai_detail = pd.DataFrame()
    assert "by_boat" not in find(ed.build_day(D, inp, generated_at=GEN), 2, 1)["prediction"]["tenkai"]
