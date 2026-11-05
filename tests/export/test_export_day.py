"""E-2: export_day を合成データ(interim・予想の parquet と同じ列)で確かめる。"""

import json
import math
import random
from datetime import date, datetime, timedelta

import pandas as pd
import pytest

from src.export import export_day as ed
from src.export.build import iter_races
from src.export.build import strip_numeric_odds
from src.export.schema import check_forbidden, validate_day

D = date(2026, 11, 2)
YMD = "20261102"
GEN = "2026-11-02T15:05:00+09:00"
# (場番号, grade_number, day_status)
STADIUMS = [(2, 5, "normal"), (12, 1, "normal"), (20, 4, "canceled")]
NOW = datetime(2026, 11, 2, 15, 5)


def _race_id(sn, rn):
    return f"{YMD}-{sn:02d}-{rn:02d}"


def make_tables(with_odds=False):
    """interim(races / entries / payouts / odds)と predictions の合成データ。"""
    rng = random.Random(7)
    races, entries, payouts, odds, preds = [], [], [], [], []
    for sn, grade, dstat in STADIUMS:
        for rn in range(1, 13):
            rid = _race_id(sn, rn)
            closed = datetime(2026, 11, 2, 10, 0) + timedelta(minutes=30 * (rn - 1))
            done = closed < NOW and dstat == "normal"
            absent = {4} if (sn, rn) == (2, 3) else set()
            races.append({
                "race_id": rid, "date": D, "stadium_number": sn, "race_number": rn,
                "closed_at": closed.strftime("%Y-%m-%d %H:%M:%S"), "grade_number": grade,
                "title": f"テスト杯{sn}", "subtitle": "予選", "distance": 1800,
                "day_number": None if dstat != "normal" else 2, "day_number_source": "2日目",
                "day_status": dstat, "technique_number": 1 if done else None,
                "is_valid_result": done, "has_refund": bool(absent), "source": "openapi",
                "n_entries": 6, "has_dead_heat": False,
                "is_canceled": True if dstat != "normal" else (False if done else None),
            })
            order = [1, 2, 3, 5, 6] if absent else [2, 1, 6, 3, 4, 5] if rn % 2 else [1, 2, 3, 4, 5, 6]
            for en in range(1, 7):
                pos = (order.index(en) + 1) if (done and en in order) else None
                entries.append({
                    "race_id": rid, "date": D, "stadium_number": sn, "race_number": rn, "entry_number": en,
                    "racer_number": 4000 + sn * 20 + en, "racer_name": f"選手 {sn}-{en}",
                    "rank_number": 1 + (en % 4), "branch_number": 13, "birthplace_number": 13,
                    "age": 30, "weight": 52.0, "flying_count": 1 if en == 5 else 0, "late_count": 0,
                    "average_start_timing": 0.1612345, "national_win_rate": 5.123456,
                    "local_win_rate": 4.9876, "motor_top_2_percent": 44.444, "pv_exhibition_time": 6.7123,
                    "pv_course_number": en, "pv_tilt_adjustment": 0.5, "pv_parts_count": 1,
                    "place_number": (pos if pos else (15 if en in absent and done else None)),
                    "finish_position": pos, "is_absent": en in absent,
                })
            if done:
                a, b, c = order[:3]
                payouts += [
                    {"race_id": rid, "bet_type": "win", "combination": str(a), "amount": 350, "label": None},
                    {"race_id": rid, "bet_type": "exacta", "combination": f"{a}-{b}", "amount": 870, "label": None},
                    {"race_id": rid, "bet_type": "trifecta", "combination": f"{a}-{b}-{c}", "amount": 2750,
                     "label": None},
                    {"race_id": rid, "bet_type": "trio", "combination": "=".join(map(str, sorted((a, b, c)))),
                     "amount": 950, "label": None},
                    {"race_id": rid, "bet_type": "quinella", "combination": "=".join(map(str, sorted((a, b)))),
                     "amount": 300, "label": None},
                ]
            if with_odds:
                for i in range(1, 7):
                    for j in range(1, 7):
                        for k in range(1, 7):
                            if len({i, j, k}) == 3:
                                odds.append({"race_id": rid, "bet_type": "trifecta",
                                             "combination": f"{i}-{j}-{k}", "odds": 10.0 + i + j + k,
                                             "odds_lower": None, "odds_upper": None})
            # 予想: (12, 5) は予想なし。(12, 11) と (12, 12) は直前予想もある
            if (sn, rn) == (12, 5):
                continue
            s = [rng.gauss(0, 0.6) + (1.5 if en == 1 else 0) for en in range(1, 7)]
            ex = [math.exp(x) for x in s]
            tot = sum(ex)
            variants = ["pre"] + (["preview"] if sn == 12 and rn >= 11 else [])
            for v in variants:
                for en in range(1, 7):
                    preds.append({
                        "race_id": rid, "entry_number": en, "model_id": f"{v}_20261101_test", "variant": v,
                        "predicted_at": pd.Timestamp("2026-11-02 07:30:00", tz="Asia/Tokyo") if v == "pre"
                        else pd.Timestamp(closed - timedelta(minutes=10), tz="Asia/Tokyo"),
                        "score": s[en - 1], "p_win": ex[en - 1] / tot, "p_top2": None, "p_top3": None,
                    })
    return (pd.DataFrame(races), pd.DataFrame(entries), pd.DataFrame(payouts), pd.DataFrame(odds),
            pd.DataFrame(preds))


def inputs(with_odds=False):
    races, entries, payouts, odds, preds = make_tables(with_odds)
    meta = {"pre_20261101_test": {"model_id": "pre_20261101_test", "periods": {"train_to": "2026-08-31"}}}
    return ed.DayInputs(races=races, entries=entries, predictions=preds, payouts=payouts, odds=odds,
                        model_meta=meta)


def find(day, sn, rn):
    return next(r for r in iter_races(day) if r["race_id"] == _race_id(sn, rn))


def test_build_day_passes_schema():
    day = ed.build_day(D, inputs(), generated_at=GEN)
    assert validate_day(day, local=True) == []
    assert validate_day(strip_numeric_odds(day)) == []
    assert day["status"] == "in_progress"
    assert day["model"] == {"model_id": "pre_20261101_test", "variant": "pre", "train_until": "2026-08-31"}
    assert [s["stadium_number"] for s in day["stadiums"]] == [2, 12, 20]
    assert day["stadiums"][1]["grade"] == "SG" and day["stadiums"][0]["grade"] == "一般"


def test_states():
    day = ed.build_day(D, inputs(), generated_at=GEN)
    assert find(day, 2, 1)["state"] == "finished"
    assert find(day, 2, 12)["state"] == "scheduled"
    assert find(day, 12, 5)["state"] == "no_prediction"
    assert find(day, 12, 12)["state"] == "preview"
    assert all(r["state"] == "canceled" for r in day["stadiums"][2]["races"])


def test_preview_has_exhibition_and_pre_does_not():
    day = ed.build_day(D, inputs(), generated_at=GEN)
    pv = find(day, 12, 12)["prediction"]
    assert pv["variant"] == "preview"
    assert pv["boats"][0]["exhibition_time"] == 6.71 and pv["boats"][0]["exhibition_course"] == 1
    assert pv["predicted_at"] == "2026-11-02T15:20:00+09:00"
    pre = find(day, 2, 12)["prediction"]
    assert pre["boats"][0]["exhibition_time"] is None


def test_boat_fields_are_rounded_and_coded():
    day = ed.build_day(D, inputs(), generated_at=GEN)
    b = find(day, 2, 1)["prediction"]["boats"][0]
    assert b["national_win_rate"] == 5.12 and b["local_win_rate"] == 4.99
    assert b["motor_top2"] == 44.4 and b["avg_st"] == 0.16
    assert b["rank"] == "A2" and b["branch"] == "東京"
    assert find(day, 2, 1)["closed_at"] == "2026-11-02T10:00:00+09:00"


def test_result_and_outcome_hand_check():
    day = ed.build_day(D, inputs(), generated_at=GEN)
    r = find(day, 2, 1)
    res, rec = r["result"], r["prediction"]["recommended"]
    assert res["order"] == [2, 1, 6] and res["technique"] == "逃げ"
    # 公開する払戻は win / exacta / trifecta だけ
    assert set(res["payouts"]) == {"win", "exacta", "trifecta"}
    hit = any(x["combination"] == "2-1-6" for x in rec)
    assert res["recommended_outcome"] == {"bets": 5, "hits": int(hit), "stake": 500,
                                          "return": 2750 if hit else 0}
    assert res["final_odds"] is None


def test_absent_refund():
    day = ed.build_day(D, inputs(), generated_at=GEN)
    r = find(day, 2, 3)
    res = r["result"]
    assert res["absent_entries"] == [4]
    n_with_4 = sum("4" in x["combination"].split("-") for x in r["prediction"]["recommended"])
    oc = res["recommended_outcome"]
    assert oc.get("refunded", 0) == n_with_4
    # prediction は書き換えない(spec 04 4章 欠場があった場合)
    assert r["prediction"]["boats"][3]["p_win"] > 0


def test_forbidden_fields_not_exported():
    day = strip_numeric_odds(ed.build_day(D, inputs(with_odds=True), generated_at=GEN))
    assert check_forbidden(day) == []
    text = json.dumps(day, ensure_ascii=False)
    # 確定オッズ・期待値の数値も公開JSONには出さない(E-7)
    for word in ("age", "weight", "tilt", "parts", "score", "trio", "quinella", "_source", "odds_lower",
                 'odds"', 'ev"'):
        assert f'"{word}' not in text, word


def test_final_odds_only_for_recommended():
    inp = inputs(with_odds=True)
    day = ed.build_day(D, inp, generated_at=GEN)
    r = find(day, 2, 1)
    fo = r["result"]["final_odds"]
    assert [x["combination"] for x in fo] == [x["combination"] for x in r["prediction"]["recommended"]]
    x = fo[0]
    i, j, k = (int(v) for v in x["combination"].split("-"))
    assert x["odds"] == 10.0 + i + j + k
    assert x["ev_rank"] in ("A", "B", "C", "D")
    assert validate_day(day, local=True) == []
    # 公開JSONでは ev_rank だけ
    pub = strip_numeric_odds(day)
    assert set(find(pub, 2, 1)["result"]["final_odds"][0]) == {"bet_type", "combination", "ev_rank"}
    assert validate_day(pub) == []
    assert validate_day(day)   # 数値入りは公開JSONとしては不合格


def test_final_status_when_all_done():
    inp = inputs(with_odds=True)
    # 全レースを結果あり・予想ありにする: 締切済みのレースだけ残す
    keep = set(inp.races[inp.races["is_valid_result"] | (inp.races["day_status"] != "normal")]["race_id"])
    inp.races = inp.races[inp.races["race_id"].isin(keep)]
    day = ed.build_day(D, inp, generated_at=GEN)
    assert day["status"] == "final"
    assert validate_day(day, local=True) == []


def test_idempotent_and_files(tmp_path):
    races, entries, payouts, odds, preds = make_tables()
    interim = tmp_path / "interim"
    for name, df in (("races", races), ("entries", entries), ("payouts", payouts)):
        (interim / name).mkdir(parents=True)
        df.to_parquet(interim / name / "2026-11.parquet", index=False)
    pdir = tmp_path / "predictions"
    pdir.mkdir()
    for v in ("pre", "preview"):
        preds[preds["variant"] == v].to_parquet(pdir / f"{YMD}_{v}.parquet", index=False)
    oo = tmp_path / "official_odds"
    loaded = ed.load_inputs(D, interim_dir=interim, predictions_dir=pdir, models_dir=tmp_path / "models",
                            official_odds_dir=oo)
    out = tmp_path / "docs"
    p1 = ed.export_day(D, out, inputs=loaded, generated_at=GEN)
    t1 = p1.read_text(encoding="utf-8")
    p2 = ed.export_day(D, out, inputs=ed.load_inputs(D, interim_dir=interim, predictions_dir=pdir,
                                                    models_dir=tmp_path / "models", official_odds_dir=oo),
                       generated_at="2026-11-02T16:00:00+09:00")
    t2 = p2.read_text(encoding="utf-8")
    assert t1.replace(GEN, "X") == t2.replace("2026-11-02T16:00:00+09:00", "X")
    day = json.loads(t1)
    assert validate_day(day) == []
    assert day["model"]["train_until"] is None   # meta.json が無いとき
    # parquet から読んでも DataFrame を直接渡したときと同じ
    direct = ed.build_day(D, ed.DayInputs(races=races, entries=entries, predictions=preds, payouts=payouts),
                          generated_at=GEN)
    direct["model"]["train_until"] = None
    assert day == json.loads(json.dumps(strip_numeric_odds(direct), ensure_ascii=False))
    # ローカルJSONも書かれている(docs → docs-local)
    assert (tmp_path / "docs-local" / "days" / f"{YMD}.json").exists()


def test_missing_races_raises():
    with pytest.raises(ValueError):
        ed.build_day(D, ed.DayInputs(races=pd.DataFrame(), entries=pd.DataFrame(), predictions=pd.DataFrame()),
                     generated_at=GEN)


def test_harville_gamma_fallback():
    assert ed.harville_gamma(None) == (1.0, 1.0)
    assert ed.harville_gamma({"harville_gamma": None, "gamma2": 0.5}) == (1.0, 1.0)
    assert ed.harville_gamma({"harville_gamma": {"gamma2": 0.85, "gamma3": 0.75}}) == (0.85, 0.75)
    assert ed.harville_gamma({"gamma2": 0.9, "gamma3": 0.8}) == (0.9, 0.8)
    assert ed.harville_gamma({"periods": {}}) == (1.0, 1.0)


def test_gamma_from_meta_changes_probs():
    pytest.importorskip("src.model.combinations")
    base = ed.build_day(D, inputs(), generated_at=GEN)
    inp = inputs()
    inp.model_meta["pre_20261101_test"]["harville_gamma"] = {"gamma2": 0.8, "gamma3": 0.7}
    g = ed.build_day(D, inp, generated_at=GEN)
    assert validate_day(g) == []
    a, b = find(base, 2, 1)["prediction"], find(g, 2, 1)["prediction"]
    assert [x["p_win"] for x in a["boats"]] == [x["p_win"] for x in b["boats"]]
    assert a["picks"]["trifecta"] != b["picks"]["trifecta"]


def test_is_canceled_na_is_not_canceled():
    inp = inputs()
    # 締切前のレースは is_canceled=NA。中止にならず scheduled のまま
    assert inp.races.loc[inp.races["race_id"] == _race_id(2, 12), "is_canceled"].isna().all()
    day = ed.build_day(D, inp, generated_at=GEN)
    assert find(day, 2, 12)["state"] == "scheduled"
    # 中止は集計から外す
    assert day["day_summary"]["races_with_result"] == sum(1 for r in iter_races(day) if r["result"])


def test_strategy_has_short_label():
    day = ed.build_day(D, inputs(), generated_at=GEN)
    assert day["strategy"]["short_label"] == "3連単 上位5点"
