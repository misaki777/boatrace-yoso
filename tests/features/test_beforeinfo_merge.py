"""公式の直前情報(beforeinfo)の合流(F-12b)のテスト。

入力の例は収集担当の期待値 tests/fixtures/beforeinfo/*.expected.json(parse_beforeinfo の出力。読むだけ)。
登録番号は架空(9001〜)なので、fixture の出走表(2026-11-02 戸田1R)の登録番号に差し替えて使う。
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pandas as pd
import pytest

from features_testlib import DAY1, DAY2, load_fixture, write_raw
from src.features import feature_sets as fs
from src.features.beforeinfo import OFFICIAL, OPENAPI, NONE, make_beforeinfo_loader
from src.features.build_features import build_today, build_training
from src.features.build_interim import build_interim, read_interim
from src.features.raw_source import make_loader

ROOT = Path(__file__).resolve().parents[2]
COLLECT_FIXTURES = ROOT / "tests" / "fixtures" / "beforeinfo"
RID = "20261102-02-01"
# fixture の 2026-11-02 戸田1R の登録番号(枠番順)
RACERS = [5242, 5114, 4034, 3370, 4421, 3863]


def official(case: str, race_id: str = RID, racers=RACERS) -> dict:
    d = json.loads((COLLECT_FIXTURES / f"{case}.expected.json").read_text(encoding="utf-8"))
    d = copy.deepcopy(d)
    d["race_id"] = race_id
    for k, b in d["boats"].items():
        b["racer_number"] = racers[int(k) - 1]
    return d


def write_official(raw_dir: Path, data: dict, name: str | None = None) -> None:
    ymd, sn, rn = data["race_id"].split("-")
    path = raw_dir / "beforeinfo" / ymd / (name or f"{sn}-{rn}.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def build(tmp_path, raw_dir, name="i"):
    out = tmp_path / name
    build_interim(DAY1, DAY2, load_raw=make_loader(raw_dir), out_dir=out, verbose=False,
                  load_beforeinfo=make_beforeinfo_loader(raw_dir))
    e = read_interim("entries", interim_dir=out).set_index(["race_id", "entry_number"])
    r = read_interim("races", interim_dir=out).set_index("race_id")
    return out, e, r


def test_official_preferred(tmp_path, raw_dir):
    write_official(raw_dir, official("ok"))
    _, e, r = build(tmp_path, raw_dir)
    b1 = e.loc[(RID, 1)]
    assert b1["pv_exhibition_time"] == pytest.approx(6.71) and b1["pv_start_timing"] == pytest.approx(0.11)
    assert b1["pv_tilt_adjustment"] == pytest.approx(-0.5) and b1["pv_weight"] == pytest.approx(52.1)
    assert b1["pv_source"] == OFFICIAL and r.loc[RID, "pv_source"] == OFFICIAL
    assert bool(e.loc[(RID, 2), "pv_propeller_new"]) and e.loc[(RID, 3), "pv_parts_count"] == 2
    assert r.loc[RID, "pv_wave_height"] == 1 and r.loc[RID, "pv_water_temperature"] == pytest.approx(18.0)
    # 公式の無いレースは Open API のまま
    assert r.loc["20261102-02-12", "pv_source"] == OPENAPI
    assert (e.xs("20261102-02-12", level=0)["pv_source"] == OPENAPI).all()


def test_openapi_only_unchanged(tmp_path, raw_dir, interim_dir):
    _, e, r = build(tmp_path, raw_dir)
    base = read_interim("entries", interim_dir=interim_dir).set_index(["race_id", "entry_number"])
    pd.testing.assert_frame_equal(e, base)
    assert (r.loc[r.index.str.startswith("20261102"), "pv_source"] == OPENAPI).all()


def test_official_only(tmp_path, raw_dir):
    raw = load_fixture("openapi", DAY2)
    raw["programs"]["stadiums"]["2"]["races"]["1"]["preview"] = None  # Open API に直前情報なし
    write_raw(raw_dir, "openapi", DAY2, raw)
    _, e0, r0 = build(tmp_path, raw_dir, "before")
    assert r0.loc[RID, "pv_source"] == NONE and e0.loc[(RID, 1), "pv_source"] == NONE
    write_official(raw_dir, official("ok"))
    _, e, r = build(tmp_path, raw_dir, "after")
    assert e.loc[(RID, 1), "pv_exhibition_time"] == pytest.approx(6.71) and r.loc[RID, "pv_source"] == OFFICIAL


def test_not_posted_falls_back_to_openapi(tmp_path, raw_dir, interim_dir):
    base = read_interim("entries", interim_dir=interim_dir).set_index(["race_id", "entry_number"])
    write_official(raw_dir, official("not_posted"))
    _, e, r = build(tmp_path, raw_dir)
    b = e.loc[(RID, 1)]
    # 展示は Open API、体重・チルトは公式(掲載済み)
    assert b["pv_exhibition_time"] == base.loc[(RID, 1), "pv_exhibition_time"]
    assert b["pv_start_timing"] == base.loc[(RID, 1), "pv_start_timing"]
    assert b["pv_weight"] == pytest.approx(52.1)
    assert b["pv_source"] == OPENAPI and r.loc[RID, "pv_source"] == OPENAPI


def test_partial(tmp_path, raw_dir, interim_dir):
    base = read_interim("entries", interim_dir=interim_dir).set_index(["race_id", "entry_number"])
    write_official(raw_dir, official("partial"))
    _, e, r = build(tmp_path, raw_dir)
    # 展示タイムは公式、スタート展示は未掲載なので Open API
    assert e.loc[(RID, 1), "pv_exhibition_time"] == pytest.approx(6.71)
    assert e.loc[(RID, 1), "pv_course_number"] == base.loc[(RID, 1), "pv_course_number"]
    # 6号艇は展示タイムも未掲載 → Open API の値、出どころも openapi
    assert e.loc[(RID, 6), "pv_exhibition_time"] == base.loc[(RID, 6), "pv_exhibition_time"]
    assert e.loc[(RID, 6), "pv_source"] == OPENAPI and e.loc[(RID, 1), "pv_source"] == OFFICIAL
    assert r.loc[RID, "pv_source"] == OFFICIAL


def test_flying_absent_maeduke(tmp_path, raw_dir):
    write_official(raw_dir, official("flying"))
    _, e, _ = build(tmp_path, raw_dir, "f")
    assert e.loc[(RID, 3), "pv_start_timing"] == pytest.approx(-0.02)   # F は負の値
    assert e.loc[(RID, 4), "pv_start_timing"] == pytest.approx(0.0)
    write_official(raw_dir, official("absent"))
    _, e, _ = build(tmp_path, raw_dir, "a")
    b4 = e.loc[(RID, 4)]
    assert pd.isna(b4["pv_exhibition_time"]) and pd.isna(b4["pv_course_number"]) and pd.isna(b4["pv_weight"])
    assert b4["pv_source"] == NONE
    assert e.loc[(RID, 6), "pv_course_number"] == 5   # 欠場の分、外の艇が詰まる
    write_official(raw_dir, official("maeduke"))
    _, e, _ = build(tmp_path, raw_dir, "m")
    assert list(e.xs(RID, level=0)["pv_course_number"]) == [1, 2, 4, 5, 6, 3]


def test_racer_mismatch_is_skipped(tmp_path, raw_dir, interim_dir):
    base = read_interim("entries", interim_dir=interim_dir).set_index(["race_id", "entry_number"])
    d = official("ok")
    d["boats"]["1"]["racer_number"] = 1234
    write_official(raw_dir, d)
    _, e, _ = build(tmp_path, raw_dir)
    assert e.loc[(RID, 1), "pv_exhibition_time"] == base.loc[(RID, 1), "pv_exhibition_time"]
    assert e.loc[(RID, 2), "pv_exhibition_time"] == pytest.approx(6.82)


def test_final_days_are_not_merged(tmp_path, raw_dir, interim_dir):
    """確定済み(turnmark)の日には合流しない(学習は Open API の確定値)。"""
    write_official(raw_dir, official("maeduke", race_id="20261101-02-01"))
    _, e, _ = build(tmp_path, raw_dir)
    base = read_interim("entries", interim_dir=interim_dir).set_index(["race_id", "entry_number"])
    pd.testing.assert_frame_equal(e.xs("20261101-02-01", level=0), base.xs("20261101-02-01", level=0))


def test_latest_file_wins(tmp_path, raw_dir):
    write_official(raw_dir, official("not_posted"), "02-01_100000.json")
    write_official(raw_dir, official("ok"), "02-01_103000.json")
    _, e, _ = build(tmp_path, raw_dir)
    assert e.loc[(RID, 1), "pv_exhibition_time"] == pytest.approx(6.71)


def _shuffle(raw):
    for st in raw["programs"]["stadiums"].values():
        for race in st["races"].values():
            rs = race["result"]["racers"]
            keys = sorted(rs)
            vals = [rs[k]["place_number"] for k in keys][::-1]
            for k, p in zip(keys, vals):
                rs[k]["place_number"], rs[k]["start_timing"] = p, 0.3
    return raw


def test_features_today_and_leak(tmp_path, raw_dir):
    """当日用は interim と同じ合流を通る。D日の結果を変えても特徴量は変わらない。"""
    write_official(raw_dir, official("maeduke"))
    v = "preview_v3"
    feats = fs.features_for(v)
    out, _, _ = build(tmp_path, raw_dir, "i1")
    full = build_training(v, DAY1, DAY2, interim_dir=out, out_dir=tmp_path / "f1")
    today = build_today(v, DAY2, interim_dir=out, out_dir=tmp_path / "t", load_raw=make_loader(raw_dir),
                        load_beforeinfo=make_beforeinfo_loader(raw_dir))
    a = full[full["date"] == DAY2].reset_index(drop=True)
    pd.testing.assert_frame_equal(a[feats], today[feats])
    r1 = a[a["race_id"] == RID].set_index("entry_number")
    assert r1.loc[6, "pv_course_number"] == 3 and r1.loc[6, "pv_course_changed"] == 1
    write_raw(raw_dir, "openapi", DAY2, _shuffle(load_fixture("openapi", DAY2)))
    out2, _, _ = build(tmp_path, raw_dir, "i2")
    b = build_training(v, DAY1, DAY2, interim_dir=out2, out_dir=tmp_path / "f2")
    pd.testing.assert_frame_equal(a[feats], b[b["date"] == DAY2].reset_index(drop=True)[feats])
