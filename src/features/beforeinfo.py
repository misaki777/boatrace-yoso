"""公式の直前情報(beforeinfo)を interim の pv_* 列に合流する(F-12b。spec/12 1.5)。

入力: data/raw/beforeinfo/YYYYMMDD/SS-RR.json(Worker が R2 に置いたものを取ってきたもの。.json.gz も可)。
      PC での手動取得の SS-RR_HHMMSS.json.gz もあれば読む(同じレースが複数あれば、名前の新しいもの)。
      形式は src/collect/BEFOREINFO_FORMAT.md(収集担当 C-8 の parse_beforeinfo の出力)。

合流の規則:
- 対象は **確定していない日(source=openapi)** だけ。turnmark・旧API(確定済みの日)は Open API の確定値を
  そのまま使う(spec/12 1.5「学習時には、過去日は Open API の確定値を使う」。学習と予想で値の出どころを
  そろえるため)。
- 項目ごとに、公式に値があれば公式、無ければ Open API の preview(公式が未掲載・一部未掲載のとき補う)。
- 公式で欠場(absent=true)の艇は、pv_* をすべて null にする(Open API でも補わない)。
- 公式の登録番号が出走表と違う艇は、公式の値を使わない(取り違え防止)。
- 単位・コードは Open API と同じ(展示ST のフライングは負の値、風向・天候のコードも同じ)。
- pv_source(艇): 展示タイムかスタート展示(進入・ST)を公式から取った艇は official、
  それ以外で Open API に直前情報があれば openapi、どちらも無ければ none。
  pv_source(レース): どれか1艇でも official なら official、そうでなければ艇の値の openapi / none。
  気象(pv_wind_* など)も、公式に値があれば公式を使う。
- 部品交換・プロペラ・体重は特徴量には使ってよいが、公開JSONには出さない(エクスポート担当の範囲)。
"""

from __future__ import annotations

import gzip
import json
from collections.abc import Callable
from datetime import date
from pathlib import Path

import pandas as pd

from src.collect import raw_io

LoadBeforeinfo = Callable[[date], dict]

OFFICIAL = "official"
OPENAPI = "openapi"
NONE = "none"

# 公式の項目 → entries の列
BOAT_FIELDS = {
    "start_course": "pv_course_number",
    "start_timing": "pv_start_timing",
    "weight": "pv_weight",
    "weight_adjustment": "pv_weight_adjustment",
    "exhibition_time": "pv_exhibition_time",
    "tilt_adjustment": "pv_tilt_adjustment",
}
WEATHER_FIELDS = ("wind_speed", "wind_direction_number", "wave_height", "weather_number",
                  "air_temperature", "water_temperature")


def beforeinfo_dir(raw_dir: Path | None = None) -> Path:
    return (Path(raw_dir) if raw_dir is not None else raw_io.RAW_DIR) / "beforeinfo"


def make_beforeinfo_loader(raw_dir: Path | None = None) -> LoadBeforeinfo:
    """1日分の公式の直前情報を {race_id: dict} で返す関数を作る。無ければ空の dict。"""
    base = beforeinfo_dir(raw_dir)

    def _load(target: date) -> dict:
        day = base / target.strftime("%Y%m%d")
        if not day.is_dir():
            return {}
        out: dict[str, dict] = {}
        for path in sorted(day.glob("*.json*")):  # 名前順 → 同じレースは後(新しい取得)で上書き
            if path.name.startswith("_") or path.name.endswith(".tmp"):
                continue
            try:
                if path.suffix == ".gz":
                    with gzip.open(path, "rt", encoding="utf-8") as f:
                        data = json.load(f)
                else:
                    data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            rid = data.get("race_id")
            if rid and str(rid).startswith(target.strftime("%Y%m%d")):
                out[str(rid)] = data
        return out

    return _load


def _num(v):
    return None if v is None else float(v)


def merge_official(tables: dict[str, pd.DataFrame], official: dict[str, dict]) -> tuple[dict[str, pd.DataFrame], dict]:
    """parse_day の結果(1日分)に公式の直前情報を合流する。(tables, 件数の記録) を返す。"""
    races, entries = tables["races"].copy(), tables["entries"].copy()
    stats = {"files": len(official), "races_official": 0, "boats_official": 0,
             "racer_mismatch": 0, "absent": 0, "by_status": {}}
    if not official:
        return {**tables, "races": races, "entries": entries}, stats
    eidx = {(r, int(e)): i for i, (r, e) in enumerate(zip(entries["race_id"], entries["entry_number"]))}
    ridx = {r: i for i, r in enumerate(races["race_id"])}
    e_rows = entries.index
    for rid, data in official.items():
        if rid not in ridx:
            continue
        st = data.get("status")
        stats["by_status"][st] = stats["by_status"].get(st, 0) + 1
        if st == "failed":
            continue
        any_official = False
        for key, b in (data.get("boats") or {}).items():
            i = eidx.get((rid, int(key)))
            if i is None or not isinstance(b, dict):
                continue
            row = e_rows[i]
            rn = entries.at[row, "racer_number"]
            if b.get("racer_number") is not None and pd.notna(rn) and int(b["racer_number"]) != int(rn):
                stats["racer_mismatch"] += 1
                continue
            if b.get("absent"):
                stats["absent"] += 1
                for col in list(BOAT_FIELDS.values()) + ["pv_propeller_new", "pv_parts_count"]:
                    entries.at[row, col] = pd.NA
                entries.at[row, "pv_source"] = NONE
                continue
            for src, col in BOAT_FIELDS.items():
                v = b.get(src)
                if v is not None:
                    entries.at[row, col] = v
            has_ex = b.get("exhibition_time") is not None or b.get("start_course") is not None
            # プロペラ・部品は、公式に体重かチルトがある(艇の行が載っている)ときだけ公式を使う
            if b.get("weight") is not None or b.get("tilt_adjustment") is not None or has_ex:
                if b.get("propeller_new") is not None:
                    entries.at[row, "pv_propeller_new"] = bool(b["propeller_new"])
                if isinstance(b.get("parts"), list):
                    entries.at[row, "pv_parts_count"] = len(b["parts"])
            if has_ex:
                entries.at[row, "pv_source"] = OFFICIAL
                any_official = True
                stats["boats_official"] += 1
        w = data.get("weather") or {}
        rrow = races.index[ridx[rid]]
        for k in WEATHER_FIELDS:
            if w.get(k) is not None:
                races.at[rrow, f"pv_{k}"] = w[k]
        if any_official:
            races.at[rrow, "pv_source"] = OFFICIAL
            stats["races_official"] += 1
    return {**tables, "races": races, "entries": entries}, stats


def load_day_tables(target: date, load_raw=None, load_beforeinfo: LoadBeforeinfo | None = None,
                    sources: tuple[str, ...] = ("openapi", "turnmark")) -> dict[str, pd.DataFrame]:
    """当日用: 生データを読んで interim と同じ表にし、openapi の日は公式の直前情報を合流する。

    build_interim と同じ規則(merge_official)を通すので、当日用と interim の pv_* はそろう。
    """
    from src.features import parse
    from src.features.raw_source import load_day, make_loader

    loaded = load_day(target, load_raw or make_loader(), sources=sources)
    if loaded is None:
        raise FileNotFoundError(f"{target} の生データがありません")
    source, raw = loaded
    t = parse.parse_day(raw, source, target)
    if source == "openapi":
        loader = load_beforeinfo or make_beforeinfo_loader()
        t, _ = merge_official(t, loader(target))
        for name, dtypes in (("races", parse.RACES_DTYPES), ("entries", parse.ENTRIES_DTYPES)):
            t[name] = parse.cast_frame(t[name], dtypes)
    return t
