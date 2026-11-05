"""tests/features 共通のフィクスチャ。

fixtures/{source}/2026/YYYYMMDD.json(読みやすさのため非圧縮)を、raw_io の保存形式
(gzip)で一時ディレクトリに書き出し、本物の load_raw で読ませる。ネットワークは使わない。
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.features.build_interim import build_interim  # noqa: E402
from src.features.raw_source import make_loader  # noqa: E402

# 補助関数は名前の衝突を避けるため features_testlib に置く
sys.path.insert(0, str(Path(__file__).resolve().parent))
from features_testlib import DAY1, DAY2, FIXTURES, load_fixture, write_raw  # noqa: E402,F401


@pytest.fixture
def raw_dir(tmp_path: Path) -> Path:
    d = tmp_path / "raw"
    write_raw(d, "turnmark", DAY1, load_fixture("turnmark", DAY1))
    write_raw(d, "openapi", DAY2, load_fixture("openapi", DAY2))
    return d


@pytest.fixture
def interim_dir(tmp_path: Path, raw_dir: Path) -> Path:
    out = tmp_path / "interim"
    build_interim(DAY1, DAY2, load_raw=make_loader(raw_dir), out_dir=out, verbose=False)
    return out
