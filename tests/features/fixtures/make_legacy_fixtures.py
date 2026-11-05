"""旧API(v3)のテスト用の小さな生JSONを作る(開発時に1回だけ手で実行する)。

    .venv/bin/python tests/features/fixtures/make_legacy_fixtures.py

data/raw/legacy の 2025-12-30・31 から、1場・2レースずつ抜き出す。選手名は伏せる。
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from src.collect import raw_io  # noqa: E402

OUT = Path(__file__).resolve().parent / "legacy"
STADIUM = 2
RACES = (1, 2)


def main() -> None:
    for d in (date(2025, 12, 30), date(2025, 12, 31)):
        for kind in ("programs", "previews", "results"):
            data = raw_io.load_legacy(kind, d)
            races = [r for r in data[kind] if r["stadium_number"] == STADIUM and r["number"] in RACES]
            for r in races:
                for b in r.get("boats") or []:
                    if "racer_name" in b:
                        b["racer_name"] = f"選手{b['racer_boat_number']}"
            path = OUT / kind / f"{d.year}" / f"{d.strftime('%Y%m%d')}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({kind: races}, ensure_ascii=False), encoding="utf-8")
            print(path, len(races))


if __name__ == "__main__":
    main()
