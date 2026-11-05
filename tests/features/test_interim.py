"""interim(races / entries / payouts / odds)と品質レポートのテスト。"""

from __future__ import annotations

import json
from datetime import date

import pandas as pd
import pytest

from features_testlib import DAY1, DAY2, load_fixture
from src.features import parse
from src.features.build_interim import build_interim, read_interim
from src.features.raw_source import load_day, make_loader


def _race(df: pd.DataFrame, race_id: str) -> pd.Series:
    return df.set_index("race_id").loc[race_id]


def test_row_counts_and_source(interim_dir):
    races = read_interim("races", interim_dir=interim_dir)
    entries = read_interim("entries", interim_dir=interim_dir)
    assert len(races) == 8
    # 1日目 4レース×6艇、2日目 6+5+6+6 艇
    assert len(entries) == 24 + 23
    by_date = races.groupby("date")["source"].unique()
    assert list(by_date[DAY1]) == ["turnmark"]
    assert list(by_date[DAY2]) == ["openapi"]
    assert races["race_id"].is_unique
    assert entries[["race_id", "entry_number"]].duplicated().sum() == 0


def test_columns_follow_contract(interim_dir):
    for table, dtypes in (("races", parse.RACES_DTYPES), ("entries", parse.ENTRIES_DTYPES),
                          ("payouts", parse.PAYOUTS_DTYPES), ("odds", parse.ODDS_DTYPES)):
        df = read_interim(table, interim_dir=interim_dir)
        assert list(df.columns) == list(dtypes)
        # _source で終わる列は day_number_source だけ
        assert [c for c in df.columns if c.endswith("_source")] in ([], ["day_number_source"])
    races = read_interim("races", interim_dir=interim_dir)
    assert str(races["closed_at"].dt.tz) == "Asia/Tokyo"
    assert races.loc[0, "race_id"] == "20261101-02-01"


def test_dead_heat_canceled_and_incomplete(interim_dir):
    races = read_interim("races", interim_dir=interim_dir)
    entries = read_interim("entries", interim_dir=interim_dir)
    # 同着1着 → 学習から外す
    r5 = _race(races, "20261101-02-05")
    assert bool(r5["has_dead_heat"]) and not bool(r5["is_valid_result"])
    # 中止(着順がすべて null)
    r6 = _race(races, "20261101-02-06")
    assert not bool(r6["is_valid_result"])
    assert entries.loc[entries["race_id"] == "20261101-02-06", "finish_position"].isna().all()
    # 5艇しかいない
    r2 = _race(races, "20261102-02-02")
    assert r2["n_entries"] == 5 and not bool(r2["is_valid_result"])
    # 通常
    assert bool(_race(races, "20261101-02-01")["is_valid_result"])


def test_is_canceled(interim_dir):
    races = read_interim("races", interim_dir=interim_dir).set_index("race_id")
    # 確定済み(turnmark)で結果が無いレース → 中止
    assert bool(races.loc["20261101-02-06", "is_canceled"])
    # 結果があるレース → 中止ではない(同着でも)
    assert not bool(races.loc["20261101-02-05", "is_canceled"])
    assert not races.loc[races.index.str.startswith("20261102"), "is_canceled"].any()


def test_is_canceled_unknown_on_unfinished_openapi():
    # 未確定の openapi で結果が無い → 結果未着と区別できないので NA。確定済みなら True
    raw = load_fixture("openapi", DAY2)
    race = raw["programs"]["stadiums"]["2"]["races"]["1"]
    for r in race["result"]["racers"].values():
        r["place_number"] = None
    t = parse.parse_day(raw, "openapi", DAY2)
    v = t["races"].set_index("race_id").loc["20261102-02-01", "is_canceled"]
    assert pd.isna(v)
    t = parse.parse_day(raw, "openapi", DAY2, is_final=True)
    assert bool(t["races"].set_index("race_id").loc["20261102-02-01", "is_canceled"])
    # 日単位で中止なら、未確定でも True
    race["day_number_source"] = "中止"
    t = parse.parse_day(raw, "openapi", DAY2)
    assert bool(t["races"].set_index("race_id").loc["20261102-02-01", "is_canceled"])


def test_final_checker_reads_fetch_log(tmp_path):
    from src.collect import raw_io
    from src.features.raw_source import make_final_checker
    raw_io.append_fetch_log({"source": "openapi", "date": "2026-11-02", "status": 200, "final": False}, tmp_path)
    raw_io.append_fetch_log({"source": "openapi", "date": "2026-11-02", "status": 200, "final": True}, tmp_path)
    chk = make_final_checker(tmp_path)
    assert chk("openapi", DAY2) is True
    assert chk("openapi", DAY1) is False
    assert chk("turnmark", DAY1) is True


def test_absent_and_disqualified(interim_dir):
    races = read_interim("races", interim_dir=interim_dir)
    e = read_interim("entries", interim_dir=interim_dir).set_index(["race_id", "entry_number"])
    # フライング(14)と返還
    f = e.loc[("20261102-02-12", 1)]
    assert f["place_number"] == 14 and bool(f["is_absent"]) and pd.isna(f["finish_position"])
    assert bool(_race(races, "20261102-02-12")["has_refund"])
    # 欠場(16)
    assert bool(e.loc[("20261102-03-02", 2), "is_absent"])
    # 不完走失格(12)は失格。欠場ではない
    dq = e.loc[("20261101-02-04", 3)]
    assert bool(dq["is_disqualified"]) and not bool(dq["is_absent"]) and pd.isna(dq["finish_position"])
    # 欠場・返還のあるレースでも、1着が1艇なら有効
    assert bool(_race(races, "20261102-02-12")["is_valid_result"])


def test_payouts(interim_dir):
    p = read_interim("payouts", interim_dir=interim_dir)
    r1 = p[p["race_id"] == "20261101-02-01"]
    tri = r1[r1["bet_type"] == "trifecta"].iloc[0]
    assert tri["combination"] == "2-1-6" and tri["amount"] == 2750
    assert set(r1["bet_type"]) == set(parse.BET_TYPES)
    # 中止レースは払戻なし
    assert (p["race_id"] == "20261101-02-06").sum() == 0


def test_payout_null_combination_is_kept():
    raw = load_fixture("openapi", DAY2)
    race = raw["programs"]["stadiums"]["2"]["races"]["1"]
    race["result"]["payouts"]["trifecta"] = [{"combination": None, "amount": 100, "label": "特払い"}]
    t = parse.parse_day(raw, "openapi", DAY2)
    p = t["payouts"]
    row = p[(p["race_id"] == "20261102-02-01") & (p["bet_type"] == "trifecta")]
    assert len(row) == 1 and pd.isna(row.iloc[0]["combination"]) and row.iloc[0]["label"] == "特払い"


def test_odds(interim_dir):
    o = read_interim("odds", interim_dir=interim_dir)
    # turnmark の3レース分(中止レースにはオッズなし)。1レース 6+6+30+15+15+120+20 行
    assert o["race_id"].nunique() == 3
    assert len(o) == 3 * 212
    counts = o[o["race_id"] == "20261101-02-01"]["bet_type"].value_counts()
    assert counts["trifecta"] == 120 and counts["trio"] == 20 and counts["exacta"] == 30
    assert o["odds"].dtype == "float32"
    # 幅のある券種は lower/upper、それ以外は odds
    rng = o[o["bet_type"].isin(["place", "quinella_place"])]
    assert rng["odds"].isna().all() and rng["odds_lower"].notna().all() and rng["odds_upper"].notna().all()
    pt = o[~o["bet_type"].isin(["place", "quinella_place"])]
    assert pt["odds"].notna().all() and pt["odds_lower"].isna().all()
    # 組合せ表記: 順序あり "-"、順不同は昇順の "="
    assert "1-2-3" in set(o.loc[o["bet_type"] == "trifecta", "combination"])
    trio = o.loc[o["bet_type"] == "trio", "combination"]
    assert all(c.split("=") == sorted(c.split("="), key=int) for c in trio)


def test_odds_nested_format_values():
    # 実データ(turnmark)と同じ入れ子形式
    section = {
        "date": "2026-11-01", "stadium_number": 2, "race_number": 1,
        "win": {"1": 1.3, "2": None},
        "trio": {"1": {"2": {"3": 3.9}}},
        "quinella_place": {"1": {"2": {"lower_limit": 1.6, "upper_limit": 1.9}}},
    }
    rows = parse._odds_rows("20261101-02-01", section)
    df = pd.DataFrame(rows)
    assert set(df["bet_type"]) == {"win", "trio", "quinella_place"}
    assert df.loc[df["combination"] == "1=2=3", "odds"].iloc[0] == pytest.approx(3.9)
    qp = df[df["bet_type"] == "quinella_place"].iloc[0]
    assert qp["combination"] == "1=2" and qp["odds_lower"] == 1.6 and qp["odds_upper"] == 1.9
    # 値が null の組合せ(欠場艇など)も行は残す
    assert pd.isna(df.loc[(df["bet_type"] == "win") & (df["combination"] == "2"), "odds"].iloc[0])


def test_quality_report(interim_dir):
    q = json.loads((interim_dir / "_quality" / "2026-11.json").read_text(encoding="utf-8"))
    assert q["races"]["total"] == 8
    assert q["races"]["dead_heat_first"] == 1
    assert q["races"]["incomplete_entries"] == 1
    assert q["races"]["with_odds"] == 3
    assert q["days"]["by_source"] == {"turnmark": 1, "openapi": 1}
    assert q["entries"]["absent"] == 2
    assert q["entries"]["disqualified"] == 1


def test_idempotent(tmp_path, raw_dir, interim_dir):
    before = {t: read_interim(t, interim_dir=interim_dir) for t in ("races", "entries", "odds")}
    build_interim(DAY1, DAY2, load_raw=make_loader(raw_dir), out_dir=interim_dir, verbose=False)
    for t, df in before.items():
        pd.testing.assert_frame_equal(df, read_interim(t, interim_dir=interim_dir))


def test_turnmark_preferred(raw_dir):
    # 同じ日に両方あれば turnmark を使う
    from features_testlib import write_raw
    write_raw(raw_dir, "turnmark", DAY2, load_fixture("openapi", DAY2))
    source, _ = load_day(DAY2, make_loader(raw_dir))
    assert source == "turnmark"


def test_propeller_and_parts_missing_key_is_null():
    raw = load_fixture("openapi", DAY2)
    pv = raw["programs"]["stadiums"]["2"]["races"]["1"]["preview"]["racers"]
    del pv["1"]["propeller"], pv["1"]["parts"]   # 古いファイル: キーが無い
    pv["2"]["propeller"] = None                     # キーはあるが null: 新品なし
    pv["3"]["propeller"] = "新"
    e = parse.parse_day(raw, "openapi", DAY2)["entries"].set_index(["race_id", "entry_number"])
    assert pd.isna(e.loc[("20261102-02-01", 1), "pv_propeller_new"])
    assert pd.isna(e.loc[("20261102-02-01", 1), "pv_parts_count"])
    assert e.loc[("20261102-02-01", 2), "pv_propeller_new"] == False  # noqa: E712
    assert e.loc[("20261102-02-01", 3), "pv_propeller_new"] == True  # noqa: E712


def test_date_mismatch_raises():
    raw = load_fixture("openapi", DAY2)
    with pytest.raises(ValueError):
        parse.parse_day(raw, "openapi", date(2026, 11, 3))
