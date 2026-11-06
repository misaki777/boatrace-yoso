"""scripts/ci_schedule.py と、lock(直前予想の確定)の CI 部分のテスト(R2・ネットワークには触らない)。"""

import os
import subprocess
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import pytest

from scripts import ci_schedule, ci_state, ci_status

ROOT = Path(__file__).resolve().parents[2]
D = date(2026, 11, 6)


def _races():
    return pd.DataFrame({
        "race_id": ["20261106-02-02", "20261106-02-01", "20261106-12-01", "20261106-03-01"],
        "date": [D] * 4,
        "stadium_number": [2, 2, 12, 3],
        "race_number": [2, 1, 1, 1],
        "closed_at": pd.to_datetime(["2026-11-06T11:15:00+09:00", "2026-11-06T10:45:00+09:00",
                                     "2026-11-06T15:00:00+09:00", None], utc=True),
        "is_canceled": pd.array([pd.NA, False, True, pd.NA], dtype="boolean"),
    })


def test_build_schedule():
    s = ci_schedule.build_schedule(_races(), D, datetime(2026, 11, 6, 7, 45, 3, tzinfo=ci_state.JST))
    assert s["date"] == "2026-11-06" and s["generated_at"] == "2026-11-06T07:45:03+09:00"
    # 締切順。中止と締切時刻の無いレースは入れない
    assert s["races"] == [
        {"race_id": "20261106-02-01", "jcd": 2, "rno": 1, "closed_at": "2026-11-06T10:45:00+09:00"},
        {"race_id": "20261106-02-02", "jcd": 2, "rno": 2, "closed_at": "2026-11-06T11:15:00+09:00"},
    ]


def test_same_races_ignores_generated_at():
    a = ci_schedule.build_schedule(_races(), D, datetime(2026, 11, 6, 7, 45, tzinfo=ci_state.JST))
    b = ci_schedule.build_schedule(_races(), D, datetime(2026, 11, 6, 9, 4, tzinfo=ci_state.JST))
    assert ci_schedule.same_races(a, b)
    r = _races()
    r.loc[0, "closed_at"] = pd.Timestamp("2026-11-06T11:20:00+09:00")  # 締切の変更
    assert not ci_schedule.same_races(a, ci_schedule.build_schedule(r, D, datetime.now(ci_state.JST)))
    assert not ci_schedule.same_races(None, a)


def test_schedule_key():
    assert ci_schedule.schedule_key(D) == "br-yoso-data/schedule/20261106.json"


def test_parse_race_ids():
    assert ci_state.parse_race_ids("20261106-02-01, 20261106-12-03 20261106-02-01", D) == [
        "20261106-02-01", "20261106-12-03"]
    assert ci_state.parse_race_ids("", D) == []
    for bad in ["20261106-02-01;rm -rf", "20261105-02-01", "20261106-25-01", "20261106-02-13", "../x"]:
        with pytest.raises(ValueError):
            ci_state.parse_race_ids(bad, D)


def test_beforeinfo_paths():
    assert ci_state.beforeinfo_key("20261106-02-01") == "br-yoso-data/beforeinfo/20261106/02-01.json"
    assert ci_state.beforeinfo_path("20261106-02-01", Path("/r")) == Path("/r/data/raw/beforeinfo/20261106/02-01.json")


def test_beforeinfo_in_recent_and_archive(tmp_path):
    for rel in ["data/raw/beforeinfo/20261106/02-01.json", "data/raw/beforeinfo/20260901/02-01.json"]:
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{}")
    files = ci_state.files_for("recent", D, tmp_path)
    assert "data/raw/beforeinfo/20261106/02-01.json" in files
    assert "data/raw/beforeinfo/20260901/02-01.json" not in files
    assert ci_state.raw_files_for_month("2026-09", tmp_path) == ["data/raw/beforeinfo/20260901/02-01.json"]


def test_status_lock_is_update():
    s = ci_status.build_status("lock", datetime(2026, 11, 6, 10, 40, tzinfo=ci_state.JST), Path("/nonexistent"))
    assert s["job"] == "update"


def test_ci_run_lock_does_not_fetch():
    env = {**os.environ, "DRY_RUN": "1"}
    out = subprocess.run(["bash", str(ROOT / "scripts/ci_run.sh"), "lock", "2026-11-06"], env=env, cwd=ROOT,
                         capture_output=True, text=True, check=True).stdout
    cmds = [line[2:] for line in out.splitlines() if line.startswith("+ ")]
    if "pre 予想が無い" in out:
        pytest.skip("pre 予想が無い環境")
    assert not any("src.collect" in c for c in cmds)  # Open API・公式サイトに触らない
    mods = [c.split()[2] for c in cmds]
    assert mods[0] == "src.features.build_interim"
    assert "src.model.predict" in mods and "src.model.predict_tenkai" in mods
    assert any("--per-race-lock" in c for c in cmds)
