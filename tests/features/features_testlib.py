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


def shift_raw(raw: dict, d: date) -> dict:
    """生JSONの日付を d に書き換えたコピー(長い期間の履歴をテストで作るため)。"""
    import copy
    raw = copy.deepcopy(raw)
    ymd = d.isoformat()
    for st in raw["programs"]["stadiums"].values():
        for race in st["races"].values():
            race["date"] = ymd
            if race.get("closed_at"):
                race["closed_at"] = ymd + race["closed_at"][10:]
            for k in ("preview", "result", "odds"):
                if isinstance(race.get(k), dict):
                    race[k]["date"] = ymd
    return raw


def write_days(raw_dir: Path, days, source: str = "turnmark", raw: dict | None = None) -> None:
    """1日目の fixture(または raw)を、days の各日に置く。"""
    raw = raw or load_fixture("turnmark", DAY1)
    for d in days:
        write_raw(raw_dir, source, d, shift_raw(raw, d))


LEGACY_DAYS = (date(2025, 12, 30), date(2025, 12, 31))  # 戸田 1R・2R。4日目 → 最終日


def load_legacy_fixture(kind: str, d: date) -> dict:
    path = FIXTURES / "legacy" / kind / f"{d.year}" / f"{d.strftime('%Y%m%d')}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def write_legacy(raw_dir: Path, d: date, parts: dict | None = None) -> None:
    """旧APIの3ファイルを raw_io の保存形式で書く。parts で中身を差し替えられる。"""
    for kind in ("programs", "previews", "results"):
        data = (parts or {}).get(kind) or load_legacy_fixture(kind, d)
        raw_io.write_raw_bytes(f"legacy_{kind}", d, json.dumps(data, ensure_ascii=False).encode("utf-8"),
                               raw_dir=raw_dir)
