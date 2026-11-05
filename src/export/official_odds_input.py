"""締切前オッズ(収集担当 C-5 の解析JSON)の読み込み(spec 07 2.6)。

- 収集担当の `src.collect.raw_io.list_official_odds(race_id)` があればそれを使う(既定の置き場所のとき)。
- 無い間(並行開発中)は、仕様どおりの置き場所を直接読む:
    data/raw/official_odds/YYYY/YYYYMMDD/SS-RR_TNN.json.gz(同じレースに複数あれば fetched_at が最新のもの)

解析JSONの形(07 2.6): race_id, closed_at, fetched_at, minutes_before_close, odds_time_label,
is_final_odds, odds(turnmark と同じ入れ子)。加えて、任意で status(late / not_targeted / stopped など)と
pages(取れたページ)を持ってよい(収集担当への依頼事項)。
"""

from __future__ import annotations

import gzip
import json
from datetime import date as Date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
OFFICIAL_ODDS_DIR = ROOT / "data" / "raw" / "official_odds"


def _read(path: Path) -> dict[str, Any] | None:
    try:
        with gzip.open(path, "rt", encoding="utf-8") if path.suffix == ".gz" else open(path, encoding="utf-8") as f:
            obj = json.load(f)
    except (OSError, ValueError):
        return None
    return obj if isinstance(obj, dict) else None


def is_pre_close(snap: dict[str, Any]) -> bool:
    """締切前に取ったオッズか。締切後に取ったもの(is_final_odds、または締切時刻より後)は使わない。"""
    if snap.get("is_final_odds"):
        return False
    mbc = snap.get("minutes_before_close")
    return mbc is None or float(mbc) >= 0


def load_day_snapshots(d: Date, base_dir: Path = OFFICIAL_ODDS_DIR,
                       use_collect: bool = True) -> dict[str, dict[str, Any]]:
    """その日の全レースの最新の解析JSONを race_id → dict で返す。無ければ空。

    - 締切後に取ったもの(is_final_odds=True など)は除く(期待値は締切前のオッズでだけ出す)。
    - その日の取得を止めていた(_stopped.json がある)ときは、解析JSONの無いレースのうち、
      止めた時刻より後に締め切るものを {"status": "stopped"} にする。止めた時刻が不明なら全部。
      (締切時刻は解析JSONに無いので、ここでは "stopped" の印だけを返し、export_day が締切時刻で絞る)
    """
    out: dict[str, dict[str, Any]] = {}
    day_dir = Path(base_dir) / f"{d:%Y}" / f"{d:%Y%m%d}"
    race_ids = set()
    if day_dir.exists():
        for p in day_dir.glob("*_T*.json*"):
            # ファイル名 SS-RR_TNN.json.gz(HTML は *.html.gz なので対象外)
            ss_rr = p.name.split("_", 1)[0]
            race_ids.add(f"{d:%Y%m%d}-{ss_rr}")
    loader = _collect_loader() if use_collect and Path(base_dir).resolve() == OFFICIAL_ODDS_DIR.resolve() else None
    for rid in sorted(race_ids):
        items = _list(loader, day_dir, rid)
        items = [x for x in items if is_pre_close(x)]
        if items:
            out[rid] = items[-1]
    return out


def load_stopped(d: Date, base_dir: Path = OFFICIAL_ODDS_DIR) -> dict[str, Any] | None:
    """その日の取得を止めていれば、その記録(_stopped.json)を返す。"""
    path = Path(base_dir) / f"{d:%Y}" / f"{d:%Y%m%d}" / "_stopped.json"
    if not path.exists():
        return None
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        obj = {}
    return obj if isinstance(obj, dict) else {}


def _collect_loader():
    try:
        from src.collect.raw_io import list_official_odds  # type: ignore
    except Exception:  # noqa: BLE001 - 未実装の間は直接読む
        return None
    return list_official_odds


def _list(loader, day_dir: Path, race_id: str) -> list[dict[str, Any]]:
    """そのレースの解析JSON(取得の古い順。fetched_at の無い late の記録は先頭側)。"""
    if loader is not None:
        return list(loader(race_id))
    ss_rr = race_id.split("-", 1)[1]
    items = []
    for p in day_dir.glob(f"{ss_rr}_T*.json*"):
        obj = _read(p)
        if obj is not None and obj.get("race_id", race_id) == race_id:
            items.append(obj)
    items.sort(key=lambda x: (x.get("fetched_at") is not None, x.get("fetched_at") or ""))
    return items
