"""scripts/ci_state.py と scripts/ci_run.sh のテスト(R2・ネットワークには触らない)。"""

import os
import subprocess
import tarfile
from datetime import date
from pathlib import Path

import pytest

from scripts import ci_state

ROOT = Path(__file__).resolve().parents[2]


def _touch(root, rel, text="x"):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


@pytest.fixture
def tree(tmp_path):
    r = tmp_path / "src"
    for rel in [
        "data/raw/openapi/2026/20260901.json.gz", "data/raw/openapi/2026/20261005.json.gz",
        "data/raw/turnmark/2026/20261104.json.gz", "data/raw/openapi/2026/20261105.json.gz",
        "data/raw/official_odds/2026/20261105/x.json", "data/raw/_fetch_log.jsonl",
        "data/interim/races/2026-09.parquet", "data/interim/races/2026-10.parquet",
        "data/interim/entries/2026-11.parquet", "data/interim/_quality/2026-11.json",
        "data/history/state_20261103.parquet", "data/history/state_20261104.parquet",
        "data/history/state_20261105.parquet", "data/history/state_20261106.parquet",
        "data/predictions/20261105_pre.parquet", "data/predictions/superseded/a.parquet",
        "data/features/today/20261105_pre.parquet",
        "models/pre/pre_20261104_db90/model.txt", "models/pre/pre_20261103_9c45/model.txt",
        "models/preview/preview_20261104_0b0e/meta.json",
        "reports/backtest/pre_20261104_db90/top_k_test/summary.json",
        "reports/backtest/pre_20261104_db90/top_k_test/bets.parquet",
    ]:
        _touch(r, rel)
    _touch(r, "models/latest", "pre_20261104_db90\n")
    _touch(r, "models/preview/latest", "preview_20261104_0b0e\n")
    return r


def test_recent_is_current_and_previous_month(tree):
    files = ci_state.files_for("recent", date(2026, 11, 5), tree)
    assert "data/raw/openapi/2026/20261005.json.gz" in files
    assert "data/raw/turnmark/2026/20261104.json.gz" in files
    assert "data/interim/races/2026-10.parquet" in files
    assert "data/raw/_fetch_log.jsonl" in files
    assert not any("2026-09" in f or "202609" in f or "official_odds" in f for f in files)


def test_recent_months_year_boundary():
    assert ci_state.recent_months(date(2027, 1, 1)) == ["2026-12", "2027-01"]


def test_history_keeps_newest(tree):
    assert ci_state.files_for("history", date(2026, 11, 5), tree) == [
        f"data/history/state_2026110{i}.parquet" for i in (4, 5, 6)]


def test_models_only_latest(tree):
    files = ci_state.files_for("models", date(2026, 11, 5), tree)
    assert "models/pre/pre_20261104_db90/model.txt" in files
    assert "models/preview/preview_20261104_0b0e/meta.json" in files
    assert "reports/backtest/pre_20261104_db90/top_k_test/summary.json" in files
    assert not any("9c45" in f or "bets.parquet" in f for f in files)


def test_roundtrip_overlay(tree, tmp_path):
    files = ci_state.files_for("recent", date(2026, 11, 5), tree)
    tar = tmp_path / "recent.tar.gz"
    n = ci_state.make_tar(files, tar, "recent", tree)
    assert ci_state.verify_tar(tar, "recent") == n
    dest = tmp_path / "dest"
    _touch(dest, "data/raw/openapi/2026/20250101.json.gz")  # 手元にだけあるファイルは消えない
    ci_state.extract_tar(tar, "recent", dest)
    assert (dest / "data/raw/openapi/2026/20261105.json.gz").exists()
    assert (dest / "data/raw/openapi/2026/20250101.json.gz").exists()


def test_archive_month(tree):
    assert ci_state.raw_files_for_month("2026-11", tree) == [
        "data/raw/openapi/2026/20261105.json.gz", "data/raw/turnmark/2026/20261104.json.gz"]
    assert ci_state.interim_files_for_month("2026-11", tree) == [
        "data/interim/_quality/2026-11.json", "data/interim/entries/2026-11.parquet"]


def test_empty_is_error(tmp_path):
    with pytest.raises(ValueError):
        ci_state.make_tar([], tmp_path / "x.tar.gz", "history", tmp_path)


def test_rejects_disallowed_paths(tmp_path):
    f = tmp_path / "x"
    f.write_text("x")
    for name in ["models/../etc/x", "data/raw/official_odds/x.json", "data/features/x.parquet"]:
        bad = tmp_path / "bad.tar.gz"
        with tarfile.open(bad, "w:gz") as t:
            t.add(f, arcname=name)
        with pytest.raises(ValueError):
            ci_state.verify_tar(bad, "recent" if "raw" in name else "models")


def test_month_range_keys():
    assert ci_state.month_range_keys("2026-11..2027-01") == ["2026-11", "2026-12", "2027-01"]
    with pytest.raises(ValueError):
        ci_state.month_range_keys("2026-1")


def test_wrangler_cmd_uses_remote():
    cmd = ci_state.wrangler_cmd("put", ci_state.state_key("models"), Path("m.tar.gz"))
    assert cmd[-1] == "--remote" and "br-yoso-data/state/models.tar.gz" in cmd


def _dry(mode, d):
    env = {**os.environ, "DRY_RUN": "1"}
    out = subprocess.run(["bash", str(ROOT / "scripts/ci_run.sh"), mode, d], env=env, cwd=ROOT,
                         capture_output=True, text=True, check=True).stdout
    return [line[2:] for line in out.splitlines() if line.startswith("+ ")]


def test_ci_run_daytime_order():
    mods = [c.split()[2] for c in _dry("daytime", "2026-11-05") if c.startswith("python")]
    if mods[3] == "src.model.predict":  # 手元に pre 予想が無い場合は準備から
        pytest.skip("pre 予想が無い環境")
    assert mods == ["src.collect.fetch", "src.features.build_interim", "src.features.build_features",
                    "src.features.build_features", "src.model.predict", "src.export.export_day",
                    "src.export.export_index"]


def test_ci_run_finalize_advances_state():
    cmds = _dry("finalize", "2026-11-04")
    assert any("history_state advance --state data/history/state_20261104.parquet" in c for c in cmds)
    assert not any("--state" in c and "build_features" in c for c in cmds)
