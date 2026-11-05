"""scripts/ci_state.py のテスト(R2・ネットワークには触らない)。"""

import tarfile

import pytest

from scripts import ci_state


def _make_root(tmp_path):
    (tmp_path / "data/raw/openapi").mkdir(parents=True)
    (tmp_path / "data/raw/openapi/a.json").write_text("{}")
    (tmp_path / "data/raw/official_odds").mkdir(parents=True)
    (tmp_path / "data/raw/official_odds/x.json").write_text("1.5")
    return tmp_path


def test_roundtrip_excludes_official_odds(tmp_path):
    root = _make_root(tmp_path / "src")
    tar = tmp_path / "raw.tar.gz"
    assert ci_state.make_tar("raw", tar, root) == 1
    assert ci_state.verify_tar(tar, "raw") == 1
    dest = tmp_path / "dest"
    (dest / "data/raw").mkdir(parents=True)
    (dest / "data/raw/old.txt").write_text("old")
    ci_state.extract_tar(tar, "raw", dest)
    assert (dest / "data/raw/openapi/a.json").exists()
    assert not (dest / "data/raw/old.txt").exists()
    assert not (dest / "data/raw/official_odds").exists()


def test_empty_dir_is_error(tmp_path):
    (tmp_path / "models").mkdir()
    with pytest.raises(ValueError):
        ci_state.make_tar("models", tmp_path / "m.tar.gz", tmp_path)


def test_verify_rejects_outside_path(tmp_path):
    bad = tmp_path / "bad.tar.gz"
    f = tmp_path / "x"
    f.write_text("x")
    with tarfile.open(bad, "w:gz") as t:
        t.add(f, arcname="models/../etc/x")
    with pytest.raises(ValueError):
        ci_state.verify_tar(bad, "models")


def test_wrangler_cmd_uses_remote():
    cmd = ci_state.wrangler_cmd("put", "models", ci_state.ROOT / "m.tar.gz")
    assert cmd[-1] == "--remote" and "br-yoso-data/state/models.tar.gz" in cmd
