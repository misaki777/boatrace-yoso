"""tests/features 用の補助関数・定数。

conftest を直接 import すると、tests/ をまとめて実行したときに
他の担当の conftest(tests/model/conftest.py など)と名前が衝突するため、
担当ごとに一意な名前のモジュールに分けている。
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.collect import raw_io  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"
DAY1 = date(2026, 11, 1)  # turnmark 形式(オッズあり)
DAY2 = date(2026, 11, 2)  # openapi 形式


def load_fixture(source: str, d: date) -> dict:
    path = FIXTURES / source / f"{d.year}" / f"{d.strftime('%Y%m%d')}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def write_raw(raw_dir: Path, source: str, d: date, data: dict) -> None:
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    raw_io.write_raw_bytes(source, d, body, raw_dir=raw_dir)
