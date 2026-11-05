"""Phase 3b の出力(spec 10): 展開予想(E-13)、選手カード(E-14)、モデルの更新履歴。

    python -m src.export.phase3b racers --date 2026-11-05     # docs/data/racers/YYYYMMDD.json(当日と前日の2日分だけ残す)
    python -m src.export.phase3b models                       # docs/data/models.json(models/history.json の写し)

展開予想は export_day が prediction.tenkai として出す(この モジュールの load_tenkai / tenkai_for_race を使う)。
"""

from __future__ import annotations

import argparse
import json
from datetime import date as Date
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from src.common import codes
from src.export.build import JST, SCHEMA_VERSION, to_jst_iso
from src.export.config import DOCS_DATA_DIR, LOCAL_DATA_DIR, ROOT
from src.export.io_util import write_json

PREDICTIONS_DIR = ROOT / "data" / "predictions"
RACER_CARDS_DIR = ROOT / "data" / "features" / "racer_cards"
MODELS_HISTORY = ROOT / "models" / "history.json"

# ---------------------------------------------------------------------------
# 展開予想(E-13。spec 10 3.2)
# ---------------------------------------------------------------------------

TENKAI_KEYS = ("nige", "sashi", "makuri", "makurisashi", "nuki", "megumare")


def load_tenkai(d: Date, predictions_dir: Path = PREDICTIONS_DIR) -> pd.DataFrame:
    """YYYYMMDD_{pre,preview}_tenkai.parquet を縦に積む(無ければ空)。variant 列が無ければファイル名から足す。"""
    frames = []
    for variant in ("pre", "preview"):
        p = Path(predictions_dir) / f"{d:%Y%m%d}_{variant}_tenkai.parquet"
        if p.exists():
            df = pd.read_parquet(p)
            if "variant" not in df.columns:
                df["variant"] = variant
            frames.append(df)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def round_to_one(values: list[float], nd: int = 3) -> list[float]:
    """小数 nd 桁に丸め、合計が1になるよう、差を最大の項目で調整する(spec 10 3.2)。"""
    total = sum(values)
    vals = [v / total for v in values] if total > 0 else values
    r = [round(v, nd) for v in vals]
    diff = round(1.0 - sum(r), nd)
    if diff:
        i = max(range(len(r)), key=lambda k: r[k])
        r[i] = round(r[i] + diff, nd)
    return r


def tenkai_for_race(tenkai: pd.DataFrame, race_id: str) -> dict[str, Any] | None:
    """そのレースの展開予想。直前版の確定行があればそれ、無ければ前日版(レースごと)。無ければ None。"""
    if tenkai.empty:
        return None
    g = tenkai[tenkai["race_id"] == race_id]
    if g.empty:
        return None
    pv, pre = g[g["variant"] == "preview"], g[g["variant"] == "pre"]
    if pv.empty and pre.empty:
        return None
    row = (pv if not pv.empty else pre).iloc[0]
    probs = round_to_one([float(row[f"p_{k}"]) for k in TENKAI_KEYS])
    from src.export.export_day import _datetime_iso, _int, _str  # 循環を避けて遅延 import

    out: dict[str, Any] = dict(zip(TENKAI_KEYS, probs))
    out["course1_entry"] = _int(row.get("course1_entry")) or 1
    out["version"] = str(row["variant"])
    out["model_id"] = _str(row.get("model_id"))
    locked = row.get("locked_at") if "locked_at" in row else None
    out["locked_at"] = _datetime_iso(locked) or _datetime_iso(row.get("predicted_at"))
    return out


# ---------------------------------------------------------------------------
# 選手カード(E-14。spec 10 4章)
# ---------------------------------------------------------------------------

# ファイルを小さくするための短いキー(フロントはこの表で読む。docs/data/racers/*.json の "keys" にも同じものを入れる)
RACER_KEYS = {
    "name": "選手名", "rank": "級別", "branch": "支部", "stadium": "今日出走する場の番号",
    "nat": "全国 [勝率, 2連率%, 3連率%]", "loc": "当地 [勝率, 2連率%, 3連率%]", "avg_st": "平均ST", "f": "F数", "l": "L数",
    "l365": "この場の直近365日 [出走数, 1着率, 2連対率, 3連対率](自前集計)",
    "course": "コース1〜6の [出走数, 1着率, 逃げ率, 差し率, まくり率, まくり差し率, 2着率, 3着率, 平均ST](自前集計)",
    "rates": "[フライング率, 出遅れ率, 前付け率](自前集計)",
    "series": "今節 [日付MMDD, R, 進入コース, 着順コード, 決まり手コード]",
    "recent": "直近10走 [日付MMDD, 場番号, 着順コード]",
}
COURSE_FIELDS = ("n", "win_rate", "nige", "sashi", "makuri", "makurisashi", "second", "third", "st_mean")


def _f(v: Any, nd: int) -> float | None:
    if v is None or (isinstance(v, float) and v != v):
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    return round(float(v), nd)


def _i(v: Any) -> int | None:
    f = _f(v, 0)
    return None if f is None else int(f)


def build_racers(d: Date, cards: pd.DataFrame, results: pd.DataFrame, generated_at: str) -> dict[str, Any]:
    """その日の選手カード。年齢・体重・出身地・モーター番号・ボート番号は入力にあっても出さない。"""
    res_by = {int(k): g for k, g in results.groupby("racer_number")} if not results.empty else {}
    racers: dict[str, Any] = {}
    for c in cards.sort_values("racer_number").to_dict("records"):
        num = int(c["racer_number"])
        rank = _i(c.get("rank_number"))
        branch = c.get("branch_name")
        if branch is None or (isinstance(branch, float) and branch != branch):
            branch = codes.prefecture_short_name(_i(c.get("branch_number")))
        card: dict[str, Any] = {
            "name": None if pd.isna(c.get("racer_name")) else str(c["racer_name"]),
            "rank": codes.RANK_NAMES.get(rank) if rank is not None else None,
            "branch": None if branch is None or (isinstance(branch, float) and branch != branch) else str(branch),
            "stadium": _i(c.get("stadium_number")),
            "nat": [_f(c.get("national_win_rate"), 2), _f(c.get("national_top_2_percent"), 1),
                    _f(c.get("national_top_3_percent"), 1)],
            "loc": [_f(c.get("local_win_rate"), 2), _f(c.get("local_top_2_percent"), 1),
                    _f(c.get("local_top_3_percent"), 1)],
            "avg_st": _f(c.get("average_start_timing"), 2),
            "f": _i(c.get("flying_count")), "l": _i(c.get("late_count")),
            "l365": [_i(c.get("local365_n")), _f(c.get("local365_win_rate"), 3), _f(c.get("local365_top2_rate"), 3),
                     _f(c.get("local365_top3_rate"), 3)],
            "course": [[_i(c.get(f"c{k}_n"))] + [_f(c.get(f"c{k}_{x}"), 3) for x in COURSE_FIELDS[1:]]
                       for k in range(1, 7)],
            "rates": [_f(c.get("f_rate"), 4), _f(c.get("late_rate"), 4), _f(c.get("maeduke_rate"), 3)],
        }
        g = res_by.get(num)
        series, recent = [], []
        if g is not None:
            g = g.sort_values(["date", "race_number"])
            for r in g.to_dict("records"):
                dd = r["date"]
                mmdd = f"{dd:%m%d}" if hasattr(dd, "strftime") else str(dd)[5:].replace("-", "")
                if r["kind"] == "series":
                    series.append([mmdd, _i(r["race_number"]), _i(r.get("course_number")), _i(r.get("place_number")),
                                   _i(r.get("technique_number"))])
                elif r["kind"] == "recent":
                    recent.append([mmdd, _i(r["stadium_number"]), _i(r.get("place_number"))])
        card["series"] = series
        card["recent"] = recent[-10:]
        racers[str(num)] = card
    return {
        "schema_version": SCHEMA_VERSION,
        "date": d.isoformat(),
        "generated_at": generated_at,
        "keys": RACER_KEYS,
        "techniques": {str(k): v for k, v in codes.TECHNIQUE_NAMES.items()},
        "racers": racers,
    }


RACER_DAYS_KEPT = 2


def export_racers(d: Date, out_dirs: tuple[Path, ...] = (DOCS_DATA_DIR, LOCAL_DATA_DIR),
                  cards_dir: Path = RACER_CARDS_DIR, generated_at: str | None = None) -> Path | None:
    """docs/data/racers/YYYYMMDD.json を書き、当日と前日以外の古いファイルを消す。入力が無ければ None。"""
    cp = Path(cards_dir) / f"{d:%Y%m%d}_cards.parquet"
    if not cp.exists():
        return None
    rp = Path(cards_dir) / f"{d:%Y%m%d}_results.parquet"
    cards = pd.read_parquet(cp)
    results = pd.read_parquet(rp) if rp.exists() else pd.DataFrame()
    obj = build_racers(d, cards, results, generated_at or to_jst_iso(datetime.now(JST)))
    keep = {f"{d - timedelta(days=i):%Y%m%d}.json" for i in range(RACER_DAYS_KEPT)}
    first = None
    for base in out_dirs:
        path = Path(base) / "racers" / f"{d:%Y%m%d}.json"
        write_json(path, obj, kind="racers")
        for old in path.parent.glob("*.json"):
            if old.name not in keep and old.name < path.name:
                old.unlink()
        first = first or path
    return first


# ---------------------------------------------------------------------------
# モデルの更新履歴(spec 10 2章、F40)
# ---------------------------------------------------------------------------

HISTORY_KEYS = ("date", "variant", "candidate", "adopted", "train_to", "valid", "metrics", "reason")


def build_models(history: list[dict[str, Any]], generated_at: str) -> dict[str, Any]:
    """models/history.json を公開用に写す。指標の数値と判定だけで、特徴量の一覧や内部のパスは出さない。"""
    out = []
    for h in history:
        item = {k: h.get(k) for k in HISTORY_KEYS}
        metrics = {}
        for name, v in (h.get("metrics") or {}).items():
            if isinstance(v, dict):
                metrics[name] = {k: (round(float(x), 4) if isinstance(x, (int, float)) and not isinstance(x, bool)
                                     else None) for k, x in v.items() if k in ("candidate", "latest")}
        item["metrics"] = metrics
        out.append(item)
    out.sort(key=lambda x: (str(x.get("date")), str(x.get("variant"))), reverse=True)
    return {"schema_version": SCHEMA_VERSION, "generated_at": generated_at, "history": out}


def export_models(out_dirs: tuple[Path, ...] = (DOCS_DATA_DIR, LOCAL_DATA_DIR), history_path: Path = MODELS_HISTORY,
                  generated_at: str | None = None) -> Path:
    history = json.loads(Path(history_path).read_text(encoding="utf-8")) if Path(history_path).exists() else []
    obj = build_models(history if isinstance(history, list) else [], generated_at or to_jst_iso(datetime.now(JST)))
    for base in out_dirs:
        write_json(Path(base) / "models.json", obj, kind="models")
    return Path(out_dirs[0]) / "models.json"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="選手カード・モデルの更新履歴の出力(spec 10)")
    ap.add_argument("what", choices=("racers", "models"))
    ap.add_argument("--date", default=None)
    args = ap.parse_args(argv)
    if args.what == "racers":
        p = export_racers(Date.fromisoformat(args.date))
        print(f"書き出し: {p}" if p else "選手カードの入力がありません")
    else:
        print(f"書き出し: {export_models()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
