"""scripts/live_loop.py のテスト(通信・子プロセスの実行なし)。"""

import gzip
import json
import os
from datetime import date, datetime

import pytest

from scripts import live_loop as ll

D = date(2026, 11, 3)


def _mods(cmds):
    return [c[2] for c in cmds]


def test_prepare_commands_order():
    cmds = ll.prepare_commands(D)
    assert _mods(cmds) == [
        "src.collect.fetch", "src.features.build_interim", "src.features.build_features",
        "src.model.predict", "src.export.export_day", "src.export.export_index",
    ]
    assert cmds[0][3:] == ["--source", "openapi", "--date", "2026-11-03"]
    assert "--variant" in cmds[2] and "pre" in cmds[2]


def test_refresh_does_not_predict():
    mods = _mods(ll.refresh_commands(D))
    assert "src.model.predict" not in mods
    assert mods[0] == "src.collect.fetch"
    assert mods[-1] == "src.export.export_index"


def test_odds_loop_command_options():
    cmd = ll.odds_loop_command(D, 5, "odds3t", "2,12", "/tmp/ids.txt", 1.5, resume=True)
    assert cmd[2] == "src.collect.official_odds"
    assert cmd[3:] == ["--date", "2026-11-03", "--loop", "--minutes-before", "5",
                       "--pages", "odds3t", "--stadiums", "2,12", "--race-ids-file", "/tmp/ids.txt",
                       "--sleep", "1.5", "--resume"]
    assert ll.odds_loop_command(D, 5, None, None, None, None)[3:] == [
        "--date", "2026-11-03", "--loop", "--minutes-before", "5"]
    # 収集担当の CLI に無いオプションは渡さない
    assert "--only-recommended" not in cmd


def test_recommended_race_ids_and_file(tmp_path):
    day = {"stadiums": [{"races": [
        {"race_id": "20261103-02-02", "prediction": {"recommended": [{"combination": "1-2-3"}]}},
        {"race_id": "20261103-02-01", "prediction": {"recommended": []}},
        {"race_id": "20261103-02-03", "prediction": None},
        {"race_id": "20261103-02-04", "prediction": {"recommended": [{"combination": "1"}]}},
    ]}]}
    ids = ll.recommended_race_ids(day)
    assert ids == ["20261103-02-02", "20261103-02-04"]
    assert ll.recommended_race_ids(None) == []
    path = ll.write_race_ids_file(ids)
    try:
        with open(path, encoding="utf-8") as f:
            assert f.read().split() == ids
    finally:
        os.unlink(path)


def test_describe_odds_exit():
    assert "--resume" in ll.describe_odds_exit(1, stopped=True)
    assert "正常" in ll.describe_odds_exit(0, stopped=False)
    assert "引数" in ll.describe_odds_exit(2, stopped=False)
    assert "一部" in ll.describe_odds_exit(1, stopped=False)


def test_stopped_marker_path(tmp_path):
    assert ll.stopped_marker(D, tmp_path) == tmp_path / "official_odds" / "2026" / "20261103" / "_stopped.json"


def test_closing_times_sorted_and_skips_null():
    raw = {"programs": {"stadiums": {
        "2": {"races": {"1": {"closed_at": "2026-11-03 10:45:00"}, "2": {"closed_at": None}}},
        "12": {"races": {"1": {"closed_at": "2026-11-03 10:30:00"}}},
    }}}
    closes = ll.closing_times(raw)
    assert [c.strftime("%H:%M") for c in closes] == ["10:30", "10:45"]
    assert closes[0].utcoffset().total_seconds() == 9 * 3600
    assert ll.closing_times(None) == []
    fin = ll.finish_time(closes, 40)
    assert fin.strftime("%H:%M") == "11:25"
    assert ll.finish_time([], 40) is None


def test_latest_odds_mtime(tmp_path):
    assert ll.latest_odds_mtime(D, tmp_path) == 0.0
    d = ll.odds_dir(D, tmp_path)
    d.mkdir(parents=True)
    (d / "02-01_odds3t_T05.html.gz").write_bytes(b"x")  # HTML は対象外
    assert ll.latest_odds_mtime(D, tmp_path) == 0.0
    f = d / "02-01_T05.json.gz"
    with gzip.open(f, "wt") as fh:
        json.dump({}, fh)
    os.utime(f, (1000, 1000))
    assert ll.latest_odds_mtime(D, tmp_path) == 1000


def test_child_env_adds_libgomp_path(monkeypatch, tmp_path):
    extra = tmp_path / "extra"
    extra.mkdir()
    monkeypatch.setattr(ll, "VENV_EXTRA_LIB", extra)
    monkeypatch.setenv("LD_LIBRARY_PATH", "/opt/x")
    env = ll.child_env()
    assert env["LD_LIBRARY_PATH"] == f"{extra}:/opt/x"
    monkeypatch.setenv("LD_LIBRARY_PATH", f"{extra}:/opt/x")
    assert ll.child_env()["LD_LIBRARY_PATH"] == f"{extra}:/opt/x"  # 二重に足さない


def test_describe_plan_mentions_request_estimate():
    opt = ll.Options(target=D)
    closes = [datetime(2026, 11, 3, 10, 30, tzinfo=ll.JST), datetime(2026, 11, 3, 20, 40, tzinfo=ll.JST)]
    text = "\n".join(ll.describe_plan(opt, closes))
    assert "2レース × 3ページ = 6" in text
    assert "21:20" in text
    no_odds = "\n".join(ll.describe_plan(ll.Options(target=D, with_odds=False), closes))
    assert "official_odds" not in no_odds


@pytest.mark.parametrize("argv", [
    ["--sleep", "0.5"], ["--minutes-before", "2"], ["--minutes-before", "20"], ["--refresh-minutes", "5"],
])
def test_parse_args_rejects_unsafe(argv):
    with pytest.raises(SystemExit):
        ll.parse_args(argv)


def test_parse_args_defaults():
    opt, dry, prep = ll.parse_args(["--date", "2026-11-03", "--dry-run"])
    assert opt.target == D and dry and not prep
    assert not opt.resume and opt.race_ids_file is None
    assert opt.with_odds and opt.minutes_before == 5 and opt.refresh_minutes == 30
