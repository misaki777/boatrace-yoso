"""scripts/ci_state.py の odds-pull(締切前オッズの記録。spec/13)のテスト(R2 には触らない)。"""

from datetime import date
from pathlib import Path

from scripts import ci_state

D = date(2026, 11, 7)


def test_odds_race_ids_validates():
    state = {"races": {"20261107-02-01": {}, "20261107-12-11": {}, "20261106-02-01": {}, "../x": {}, "_state": {}}}
    assert ci_state.odds_race_ids(state, D) == ["20261107-02-01", "20261107-12-11"]
    assert ci_state.odds_race_ids({}, D) == []


def test_odds_paths():
    assert ci_state.odds_key("20261107-02-01") == "br-yoso-data/odds/20261107/02-01.json"
    assert ci_state.odds_path("20261107-02-01", Path("/r")) == Path("/r/data/raw/odds_record/20261107/02-01.json")


def test_odds_record_in_recent_and_archive(tmp_path):
    for rel in ["data/raw/odds_record/20261107/02-01.json", "data/raw/odds_record/20261107/_state.json",
                "data/raw/odds_record/20260801/02-01.json"]:
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{}")
    files = ci_state.files_for("recent", date(2026, 11, 8), tmp_path)
    assert "data/raw/odds_record/20261107/02-01.json" in files
    assert not any("_state" in f or "20260801" in f for f in files)
    assert ci_state.raw_files_for_month("2026-11", tmp_path) == ["data/raw/odds_record/20261107/02-01.json"]
    assert ci_state.is_allowed("data/raw/odds_record/20261107/02-01.json", "archive-raw")


def test_odds_pull_day(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(ci_state, "ROOT", tmp_path)
    monkeypatch.setattr(ci_state, "odds_path", lambda rid, root=None: ci_state.ROOT.joinpath(
        ci_state.ODDS_RECORD_DIR, rid[:8], rid[9:] + ".json"))
    secret = 123.4
    calls = []

    def fake_get(key, dest):
        calls.append(key)
        if key.endswith("_state.json"):
            return {"races": {"20261107-02-01": {}, "20261107-02-02": {}, "20261107-03-01": {}}}
        if key.endswith("02-02.json"):
            return None  # R2 に無い
        rid = "20261107-" + key.rsplit("/", 1)[1][:-5]
        if rid == "20261107-03-01":
            rid = "20261107-09-09"  # 中身が別のレース
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text("{}")
        return {"race_id": rid, "odds": {"win": [secret]}}

    monkeypatch.setattr(ci_state, "_get_json", fake_get)
    got, n = ci_state.odds_pull_day(D, dry_run=False)
    assert (got, n) == (1, 3)
    assert (tmp_path / "data/raw/odds_record/20261107/02-01.json").exists()
    assert not (tmp_path / "data/raw/odds_record/20261107/03-01.json").exists()
    assert str(secret) not in capsys.readouterr().out  # オッズの数値をログに出さない
    # 2回目は手元にある分を取らない
    calls.clear()
    ci_state.odds_pull_day(D, dry_run=False)
    assert not any(k.endswith("02-01.json") for k in calls)


def test_odds_pull_dry_run_no_r2(monkeypatch):
    monkeypatch.setattr(ci_state, "_get_json", lambda *a: (_ for _ in ()).throw(AssertionError("R2 に触った")))
    assert ci_state.main(["odds-pull", "--dry-run", "--from", "2026-11-07", "--to", "2026-11-08"]) == 0
