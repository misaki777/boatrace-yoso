"""旧API(v3)→ interim の変換(F-7)のテスト。"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from features_testlib import DAY1, LEGACY_DAYS, load_fixture, load_legacy_fixture, write_legacy, write_raw
from src.features import parse
from src.features.build_interim import build_interim, read_interim
from src.features.legacy import day_number_of, normalize_day_label, to_v1
from src.features.raw_source import load_day, make_loader


@pytest.fixture
def legacy_interim(tmp_path):
    raw_dir = tmp_path / "raw"
    for d in LEGACY_DAYS:
        write_legacy(raw_dir, d)
    write_raw(raw_dir, "turnmark", DAY1, load_fixture("turnmark", DAY1))
    out = tmp_path / "interim"
    build_interim(LEGACY_DAYS[0], DAY1, load_raw=make_loader(raw_dir), out_dir=out, verbose=False)
    return raw_dir, out


def test_day_label():
    assert normalize_day_label("２日目") == "2日目"
    assert day_number_of("初日") == 1 and day_number_of("４日目") == 4
    assert day_number_of("最終日") is None and day_number_of("中止") is None


def test_mapping_values():
    d = LEGACY_DAYS[1]
    parts = {k: load_legacy_fixture(k, d) for k in ("programs", "previews", "results")}
    v1 = to_v1(parts["programs"], parts["previews"], parts["results"])
    t = parse.parse_day(v1, "legacy", d, is_final=True)
    p = parts["programs"]["programs"][0]
    b = p["boats"][0]
    e = t["entries"].set_index(["race_id", "entry_number"]).loc[(f"20251231-02-{p['number']:02d}", 1)]
    assert e["racer_number"] == b["racer_number"]
    assert e["rank_number"] == b["racer_class_number"]
    assert e["national_win_rate"] == pytest.approx(b["racer_national_top_1_percent"])
    assert e["local_win_rate"] == pytest.approx(b["racer_local_top_1_percent"])
    assert e["motor_number"] == b["racer_assigned_motor_number"]
    rs = next(x for x in parts["results"]["results"] if x["number"] == p["number"])
    rb = next(x for x in rs["boats"] if x["racer_boat_number"] == 1)
    assert e["place_number"] == rb["racer_place_number"]
    assert e["rs_course_number"] == rb["racer_course_number"]
    # 部品交換・プロペラは旧APIに無い → 欠損(False / 0 にしない)
    assert pd.isna(e["pv_propeller_new"]) and pd.isna(e["pv_parts_count"])
    assert pd.notna(e["pv_exhibition_time"])
    r = t["races"].set_index("race_id").loc[f"20251231-02-{p['number']:02d}"]
    assert r["day_number_source"] == "最終日" and r["technique_number"] == rs["technique_number"]
    pay = t["payouts"]
    assert set(pay["bet_type"]) == set(parse.BET_TYPES)
    assert len(t["odds"]) == 0


def test_missing_average_st_is_zero_like_v1():
    d = LEGACY_DAYS[1]
    parts = {k: load_legacy_fixture(k, d) for k in ("programs", "previews", "results")}
    parts["programs"]["programs"][0]["boats"][0]["racer_average_start_timing"] = None
    v1 = to_v1(parts["programs"], parts["previews"], parts["results"])
    e = parse.parse_day(v1, "legacy", d)["entries"]
    assert e["average_start_timing"].notna().all() and (e["average_start_timing"] == 0).sum() >= 1


def test_interim_same_columns_and_types(legacy_interim):
    _, out = legacy_interim
    for table, dtypes in (("races", parse.RACES_DTYPES), ("entries", parse.ENTRIES_DTYPES),
                          ("payouts", parse.PAYOUTS_DTYPES)):
        df = read_interim(table, interim_dir=out)
        assert list(df.columns) == list(dtypes)
        # 旧APIの月と turnmark の月で、parquet の型がそろっている
        a = pd.read_parquet(out / table / "2025-12.parquet")
        b = pd.read_parquet(out / table / "2026-11.parquet")
        assert a.dtypes.to_dict() == b.dtypes.to_dict()
    races = read_interim("races", interim_dir=out)
    assert set(races.loc[races["date"] < date(2026, 1, 1), "source"]) == {"legacy"}
    assert races.loc[races["date"] < date(2026, 1, 1), "is_valid_result"].all()


def test_final_day_number_filled(legacy_interim):
    _, out = legacy_interim
    races = read_interim("races", interim_dir=out).set_index("race_id")
    assert races.loc["20251230-02-01", "day_number"] == 4
    assert races.loc["20251231-02-01", "day_number"] == 5   # 最終日 = 前日 + 1


def test_final_day_across_month(tmp_path):
    """月の初日が最終日でも、前の月のファイルから埋める。"""
    raw_dir = tmp_path / "raw"
    d1, d2 = date(2025, 11, 30), date(2025, 12, 1)
    for src_day, dst in zip(LEGACY_DAYS, (d1, d2)):
        parts = {}
        for k in ("programs", "previews", "results"):
            data = load_legacy_fixture(k, src_day)
            for r in data[k]:
                r["date"] = dst.isoformat()
                if r.get("closed_at"):
                    r["closed_at"] = dst.isoformat() + r["closed_at"][10:]
            parts[k] = data
        write_legacy(raw_dir, dst, parts)
    out = tmp_path / "interim"
    build_interim(d1, d2, load_raw=make_loader(raw_dir), out_dir=out, verbose=False)
    races = read_interim("races", interim_dir=out).set_index("race_id")
    assert races.loc["20251201-02-01", "day_number"] == 5


def test_source_priority(legacy_interim):
    raw_dir, _ = legacy_interim
    # 同じ日に turnmark があれば turnmark(2026年以降の値は変わらない)
    write_legacy(raw_dir, DAY1, {k: load_legacy_fixture(k, LEGACY_DAYS[0])
                                 for k in ("programs", "previews", "results")})  # 中身は何でもよい
    source, _ = load_day(DAY1, make_loader(raw_dir))
    assert source == "turnmark"
    source, _ = load_day(LEGACY_DAYS[0], make_loader(raw_dir))
    assert source == "legacy"


def test_features_run_on_legacy(tmp_path, legacy_interim):
    from src.features.build_features import build_training
    _, out = legacy_interim
    f = build_training("pre_v2", LEGACY_DAYS[0], DAY1, interim_dir=out, out_dir=tmp_path / "f")
    assert (f["date"] == LEGACY_DAYS[1]).any()
    # 12/31 の H は 12/30 の旧APIの結果から作られる
    assert (f.loc[f["date"] == LEGACY_DAYS[1], "h_racer_n_90d"] > 0).any()
