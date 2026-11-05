"""生データ(data/raw/)の読み込み口。

読み込みは収集担当の契約 `src.collect.raw_io.load_raw(source, date, raw_dir=None)` に従う。
移行前の置き場所(data/raw/YYYYMMDD.json。openapi 形式)だけは、C-1 の移行が済むまで
ここで代わりに読む。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date
from pathlib import Path

from src.collect import raw_io

LoadRaw = Callable[[str, date], "dict | None"]

# 過去日の正本は turnmark。無ければ openapi で代わりにする(02_features.md 1章)
# 2025年以前は旧API(legacy。F-7)。openapi は当日用なので最後
SOURCE_PRIORITY = ("turnmark", "legacy", "openapi")


def make_loader(raw_dir: Path | None = None) -> LoadRaw:
    """raw_io.load_raw を使うローダーを返す。raw_dir を渡すとそこを読む(テスト用)。"""
    base = Path(raw_dir) if raw_dir is not None else raw_io.RAW_DIR

    def _load(source: str, target: date) -> dict | None:
        if source == "legacy":
            from src.features.legacy import to_v1
            parts = {k: raw_io.load_legacy(k, target, raw_dir=base) for k in ("programs", "previews", "results")}
            return to_v1(parts["programs"], parts["previews"], parts["results"])
        data = raw_io.load_raw(source, target, raw_dir=base)
        if data is None and source == "openapi":
            legacy = base / f"{target.strftime('%Y%m%d')}.json"
            if legacy.exists():
                data = json.loads(legacy.read_text(encoding="utf-8"))
        return data

    return _load


def load_day(target: date, load_raw: LoadRaw | None = None,
             sources: tuple[str, ...] = SOURCE_PRIORITY) -> tuple[str, dict] | None:
    """その日に使うソースを選んで読む。(source, data) を返す。どちらも無ければ None。

    turnmark は 200 が返った時点で確定(final)扱いなので、あればそれを使う。
    """
    load_raw = load_raw or make_loader()
    for source in sources:
        data = load_raw(source, target)
        if data is not None:
            return source, data
    return None


def make_final_checker(raw_dir: Path | None = None) -> Callable[[str, date], bool]:
    """取得ログ(_fetch_log.jsonl)の最新の 200 の記録から、そのファイルが確定済みかを返す関数を作る。

    turnmark は常に確定。ログに記録が無い openapi は未確定とみなす。
    """
    records = raw_io.read_fetch_log(Path(raw_dir) if raw_dir is not None else None)
    latest: dict[tuple[str, str], bool] = {}
    for r in records:
        if r.get("status") == 200:
            latest[(r.get("source"), r.get("date"))] = bool(r.get("final"))

    def _is_final(source: str, target: date) -> bool:
        if source in ("turnmark", "legacy"):
            return True
        return latest.get((source, target.isoformat()), False)

    return _is_final
