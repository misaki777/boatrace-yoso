"""E-11 / E-12: 確定版からの出力、reasons・skip_reason・preview_info、performance の追加項目、公開してはいけない項目。"""

import copy
import json

import pandas as pd

from src.export import export_day as ed
from src.export.aggregate import build_performance
from src.export.build import iter_races, strip_numeric_odds
from src.export.schema import check_forbidden, validate_day, validate_performance
from tests.export.test_export_day import D, GEN, YMD, find, inputs, make_tables


def _contribs(preds):
    rows = []
    for rid in preds["race_id"].unique():
        for en in range(1, 7):
            for rank, (feat, c) in enumerate([("national_win_rate", 0.61), ("h_stadium_course_win_rate_365d", -0.3),
                                              ("motor_top_2_percent", 0.05)], start=1):
                rows.append({"race_id": rid, "entry_number": en, "rank": rank, "feature": feat,
                             "contribution": c * (1 if en % 2 else -1), "variant": "pre"})
    return pd.DataFrame(rows)


def test_prediction_unchanged_across_reexports(tmp_path):
    """結果が後から入っても、モデルが変わっても、prediction は確定版のまま(08 5.1)。"""
    inp = inputs()
    first = ed.build_day(D, inp, generated_at=GEN)
    # 結果・払戻をすべて消した日(当日朝)と比べる
    morning = inputs()
    morning.payouts = morning.payouts.iloc[0:0]
    morning.entries["finish_position"] = None
    early = ed.build_day(D, morning, generated_at="2026-11-02T07:40:00+09:00")
    # model_meta(=models/latest)が変わっても
    later_inp = inputs()
    later_inp.model_meta = {"pre_20261101_test": {"periods": {"train_to": "2026-10-31"}}}
    later = ed.build_day(D, later_inp, generated_at="2026-11-03T09:00:00+09:00")
    for a, b, c in zip(iter_races(first), iter_races(early), iter_races(later)):
        assert a["prediction"] == b["prediction"] == c["prediction"], a["race_id"]
    pr = find(first, 2, 1)["prediction"]
    assert pr["version"] == "pre" and pr["model_id"] == "pre_20261101_test"
    assert pr["locked_at"] == "2026-11-02T07:30:00+09:00"


def test_locked_at_column_used():
    inp = inputs()
    inp.predictions["locked_at"] = pd.Timestamp("2026-11-01 21:00:00", tz="Asia/Tokyo")
    day = ed.build_day(D, inp, generated_at=GEN)
    assert find(day, 2, 1)["prediction"]["locked_at"] == "2026-11-01T21:00:00+09:00"


def test_preview_file_pre_rows_are_ignored_when_pre_file_exists(tmp_path):
    races, entries, payouts, odds, preds = make_tables()
    pdir = tmp_path / "pred"
    pdir.mkdir()
    pre = preds[preds["variant"] == "pre"]
    pre.to_parquet(pdir / f"{YMD}_pre.parquet", index=False)
    # 直前版のファイルに、直前情報が欠けて pre で予想した行が混ざる(M の仕様)
    mixed = pd.concat([preds[preds["variant"] == "preview"], pre.assign(score=pre["score"] + 9)])
    mixed.to_parquet(pdir / f"{YMD}_preview.parquet", index=False)
    interim = tmp_path / "interim"
    for name, df in (("races", races), ("entries", entries), ("payouts", payouts)):
        (interim / name).mkdir(parents=True)
        df.to_parquet(interim / name / "2026-11.parquet", index=False)
    loaded = ed.load_inputs(D, interim_dir=interim, predictions_dir=pdir, models_dir=tmp_path,
                            official_odds_dir=tmp_path / "oo")
    assert (loaded.predictions.groupby(["race_id", "variant"]).size() == 6).all()
    day = ed.build_day(D, loaded, generated_at=GEN)
    assert find(day, 12, 12)["prediction"]["version"] == "preview"
    assert find(day, 2, 1)["prediction"]["version"] == "pre"


def test_reasons_are_staged_without_raw_contribution():
    inp = inputs()
    inp.contribs = _contribs(inp.predictions[inp.predictions["variant"] == "pre"])
    day = ed.build_day(D, inp, generated_at=GEN)
    rs = find(day, 2, 1)["prediction"]["boats"][0]["reasons"]
    assert rs[0] == {"label": rs[0]["label"], "value": 5.12, "direction": "up", "strength": 3}
    assert rs[1]["value"] is None and rs[1]["direction"] == "down" and rs[1]["strength"] == 2   # 自前集計は値なし
    assert rs[2]["strength"] == 1
    assert validate_day(day, local=True) == []
    text = json.dumps(strip_numeric_odds(day))
    assert "contribution" not in text and ": 0.61," not in text and ": -0.3," not in text


def test_reasons_m10_format():
    """M-10 の contrib(label・value・value_public)を使う。非公開の値と1号艇の内側の強さ(0)は出さない。"""
    inp = inputs()
    rows = []
    for en in range(1, 7):
        rows += [
            {"race_id": f"{YMD}-02-01", "entry_number": en, "rank": 1, "feature": "r_inner_strength",
             "contribution": 1.02, "label": "内側の艇の強さ", "value": 0.0 if en == 1 else 4.76,
             "value_public": True, "model_id": "pre_20261101_test"},
            {"race_id": f"{YMD}-02-01", "entry_number": en, "rank": 2, "feature": "age", "contribution": -0.25,
             "label": "年齢", "value": 47.0, "value_public": False, "model_id": "pre_20261101_test"},
            {"race_id": f"{YMD}-02-01", "entry_number": en, "rank": 3, "feature": "h_racer_course_win_rate",
             "contribution": 0.1, "label": "このコースでの1着率", "value": 0.4131250381, "value_public": True,
             "model_id": "pre_20261101_test"},
        ]
    inp.contribs = pd.DataFrame(rows).assign(variant="pre")
    day = ed.build_day(D, inp, generated_at=GEN)
    b1, b2 = find(day, 2, 1)["prediction"]["boats"][:2]
    assert [r["label"] for r in b1["reasons"]] == ["年齢", "このコースでの1着率"]
    assert b1["reasons"][0] == {"label": "年齢", "value": None, "direction": "down", "strength": 2}
    assert b1["reasons"][1]["value"] == 0.413
    assert b2["reasons"][0] == {"label": "内側の艇の強さ", "value": 4.76, "direction": "up", "strength": 3}
    assert "47" not in json.dumps(b1)


def test_skip_reason_and_preview_info():
    inp = inputs()
    strategy = {"name": "confidence_skip", "bet_type": "trifecta", "k": 5, "alpha": 0.99, "description": "x"}
    day = ed.build_day(D, inp, generated_at=GEN, strategy=strategy)
    pr = find(day, 2, 1)["prediction"]
    assert pr["recommended"] == [] and pr["skip_reason"] == "low_confidence"
    assert validate_day(day, local=True) == []
    pi = find(day, 2, 1)["preview_info"]
    assert pi["boats"][0] == {"entry": 1, "exhibition_time": 6.71, "exhibition_course": 1, "exhibition_st": None}


def test_forbidden_personal_and_contrib():
    day = ed.build_day(D, inputs(), generated_at=GEN)
    for key, val in (("marks", {"1": "◎"}), ("memo", "x"), ("purchases", []), ("budget", 1000),
                     ("contribution", 0.5), ("notes", "x")):
        bad = copy.deepcopy(day)
        find(bad, 2, 1)[key] = val
        assert check_forbidden(bad), key
    bad = copy.deepcopy(day)
    find(bad, 2, 1)["prediction"]["boats"][0]["reasons"] = [
        {"label": "全国勝率", "value": 1.0, "direction": "up", "strength": 3, "raw": 0.61}]
    assert validate_day(bad, local=True)


def test_live_performance_3a():
    day = strip_numeric_odds(ed.build_day(D, inputs(), generated_at=GEN))
    perf = build_performance([day], GEN, None)
    assert validate_performance(perf) == []
    lv = perf["live"]
    assert lv["sample_note"] == "few"
    names = [s["name"] for s in lv["strategies"]]
    assert names == ["top_k", "baseline_win1", "baseline_tri1_top5"]
    # 1号艇 単勝: 結果のある各レースで1点。手計算(合成データの払戻は win 350 円、1号艇が勝つのは偶数R)
    rows = [r for r in iter_races(day) if r["result"] and r["prediction"]]
    wins = sum(1 for r in rows if r["result"]["order"][0] == 1)
    b = lv["strategies"][1]
    assert b["races"] == len(rows) and b["roi"] == round(wins * 350 / (len(rows) * 100), 3)
    assert lv["models"] == [{"model_id": "pre_20261101_test", "from": "2026-11-02", "to": "2026-11-02"},
                            {"model_id": "preview_20261101_test", "from": "2026-11-02", "to": "2026-11-02"}]
    assert sum(x["n"] for x in lv["calibration"]) == 6 * sum(1 for r in rows if not r["result"]["absent_entries"])


def _write_inputs(tmp_path, preds_pre, preds_preview):
    races, entries, payouts, _, _ = make_tables()
    pdir = tmp_path / "pred"
    pdir.mkdir(exist_ok=True)
    preds_pre.to_parquet(pdir / f"{YMD}_pre.parquet", index=False)
    (pdir / f"{YMD}_preview.parquet").unlink(missing_ok=True)
    if preds_preview is not None:
        preds_preview.to_parquet(pdir / f"{YMD}_preview.parquet", index=False)
    interim = tmp_path / "interim"
    for name, df in (("races", races), ("entries", entries), ("payouts", payouts)):
        (interim / name).mkdir(parents=True, exist_ok=True)
        df.to_parquet(interim / name / "2026-11.parquet", index=False)
    return ed.load_inputs(D, interim_dir=interim, predictions_dir=pdir, models_dir=tmp_path,
                          official_odds_dir=tmp_path / "oo")


def test_per_race_preview_lock_appended_later(tmp_path):
    """直前版はレースごとに確定・追記される(--per-race-lock)。追記の前後で、確定済みの予想は変わらない。"""
    _, _, _, _, preds = make_tables()
    pre = preds[preds["variant"] == "pre"].copy()
    pre["locked_at"] = pd.Timestamp("2026-11-02 07:30:00", tz="Asia/Tokyo")
    # 直前版にする元: 場12の全レースに preview の行を作り、締切12分前に確定したことにする
    pv_all = pre[pre["race_id"].str.startswith(f"{YMD}-12-")].copy()
    pv_all["variant"] = "preview"
    pv_all["model_id"] = "preview_x"
    pv_all["p_win"] = pv_all.groupby("race_id")["p_win"].transform(lambda s: s[::-1].values)
    rn = pv_all["race_id"].str[-2:].astype(int)
    pv_all["locked_at"] = pd.Timestamp("2026-11-02 09:48:00", tz="Asia/Tokyo") + pd.to_timedelta(30 * (rn - 1), "m")
    pv_all["predicted_at"] = pv_all["locked_at"]
    first = pv_all[rn <= 3]          # 1回目: 1〜3R だけ直前版が確定
    second = pv_all[rn <= 6]         # 2回目: 4〜6R が追記された(1〜3R の行はそのまま)

    def run(preview):
        return ed.build_day(D, _write_inputs(tmp_path, pre, preview), generated_at=GEN)

    d0, d1, d2 = run(None), run(first), run(second)
    for rn_ in [x for x in range(1, 13) if x != 5]:   # 12-05 は合成データで予想なし
        v0, v1, v2 = (find(d, 12, rn_)["prediction"] for d in (d0, d1, d2))
        assert v0["version"] == "pre" and v0["locked_at"] == "2026-11-02T07:30:00+09:00"
        assert v1["version"] == ("preview" if rn_ <= 3 else "pre")
        assert v2["version"] == ("preview" if rn_ <= 6 else "pre")
        if rn_ <= 3:
            assert v1 == v2                               # 確定済みの直前版は追記で変わらない
            assert v1["locked_at"] == f"2026-11-02T{9 + (48 + 30 * (rn_ - 1)) // 60:02d}:" \
                                      f"{(48 + 30 * (rn_ - 1)) % 60:02d}:00+09:00"
            assert v1["model_id"] == "preview_x"
        elif rn_ > 6:
            assert v0 == v1 == v2                         # 前日版のままのレースも変わらない
    # 他の場は前日版のまま
    assert all(r["prediction"]["version"] == "pre" for r in d2["stadiums"][0]["races"])
    assert validate_day(d2, local=True) == []
    # live の by_version はレース単位(場12の1〜6Rのうち結果のあるものだけが直前版)
    perf = build_performance([strip_numeric_odds(d2)], GEN, None)
    n_preview = sum(1 for r in iter_races(d2) if r["result"] and r["prediction"]
                    and r["prediction"]["version"] == "preview"
                    and r["prediction"]["recommended"])
    assert (perf["live"]["by_version"]["preview"] or {"races": 0})["races"] == n_preview
