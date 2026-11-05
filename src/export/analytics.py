"""分析用の公開JSON(E-15、spec 11)を docs/data/analytics/ に書き出す。

    python -m src.export.analytics --what stadiums
    python -m src.export.analytics --what rankings --date 2026-11-05
    python -m src.export.analytics --what cube
    python -m src.export.analytics --what model [--from 2026-11-02 --to 2026-11-04]
    python -m src.export.analytics --what weekly --week 2026-W45
    python -m src.export.analytics --what simulator [--from ... --to ...]
    python -m src.export.analytics --what all --date D          # 上の全部(週は前の週)
    python -m src.export.analytics --what all --date D --sample # docs/data/sample(-local)/analytics/ に作る

- 入力: 特徴量担当の集計 data/features/analytics/{stadiums,rankings,cube}/、
        モデル担当の関数 src.model.analytics_eval(build_model_weakness / build_weekly)、src.model.simulator。
- 共通ルール src/common/analytics_rules.py: 件数30未満の行は率を出さない(件数だけ)。サイズ上限(gzip後)を
  超えたら SizeLimitExceeded で失敗させる(黙って大きなファイルを出さない)。
- 公開してはいけない項目(オッズの数値・購入記録・印・メモ・予算など)が無いことを書き出す前に確かめる。
- 書き出すたびに analytics/index.json(一覧・更新時刻・期間・週の一覧)を作り直す。ローカル表示用に docs/data-local/ にも同じものを書く。
"""

from __future__ import annotations

import argparse
import gzip
import json
import math
from datetime import date as Date
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from src.common import analytics_rules as ar
from src.common import codes
from src.export.build import JST, SCHEMA_VERSION, to_jst_iso
from src.export.config import DOCS_DATA_DIR, LOCAL_DATA_DIR, ROOT
from src.export.io_util import dumps
from src.export.schema import SchemaError, check_forbidden

ANALYTICS_IN = ROOT / "data" / "features" / "analytics"
SUBDIR = "analytics"
RANKING_DAYS_KEPT = 2
# ランキングの件数の下限(統括の決定 2026-11-05): 今節の平均着順は2走以上、モーターは公式値なので下限なし、
# それ以外は30(spec 11 2.3)。件数は行に入れて出し、少ないものはアプリで薄く表示する
RANKING_MIN_COUNT = {"series": 2, "motor": 0}

# 率の列(件数が足りないときに null にする)と、件数の列
RATE_SUFFIXES = ("_rate", "_lo", "_hi")
RATE_PREFIXES = ("share_",)
COUNT_COLS = ("n", "n_races", "n_starts", "c1_n", "n2")


class AnalyticsError(ValueError):
    pass


# ---------------------------------------------------------------------------
# 共通
# ---------------------------------------------------------------------------

def clean(v: Any, nd: int = 4) -> Any:
    """JSON に出せる値にする(numpy・pandas の型、NaN、日付)。小数は nd 桁。"""
    if isinstance(v, dict):
        return {str(k): clean(x, nd) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [clean(x, nd) for x in v]
    if v is None:
        return None
    if hasattr(v, "item") and not isinstance(v, (str, bytes)):
        try:
            v = v.item()
        except (ValueError, AttributeError):
            pass
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(v, bool) or isinstance(v, (int, str)):
        return v
    if isinstance(v, float):
        return round(v, nd) if math.isfinite(v) else None
    if isinstance(v, (Date, datetime, pd.Timestamp)):
        return v.isoformat()[:10] if not isinstance(v, datetime) or isinstance(v, pd.Timestamp) else v.isoformat()
    return str(v)


def _is_rate(col: str) -> bool:
    return col.startswith(RATE_PREFIXES) or col.endswith(RATE_SUFFIXES) or col.endswith("_diff_national") \
        or col in ("manshu_rate",)


def records(df: pd.DataFrame, n_col: str, drop: tuple[str, ...] = ()) -> list[dict[str, Any]]:
    """表を行の dict にする。n_col の件数が30未満の行は、率の列を null にする(件数だけ。spec 11 1.3)。"""
    out = []
    for r in df.to_dict("records"):
        row = {k: clean(v) for k, v in r.items() if k not in drop}
        n = row.get(n_col)
        level = ar.sample_level(n)
        row["level"] = level
        if level == ar.SAMPLE_INSUFFICIENT:
            for k in list(row):
                if _is_rate(k) or k in ("trifecta_mean", "trifecta_median", "value", "value2"):
                    row[k] = None
        out.append(row)
    return out


def write_analytics(obj: dict[str, Any], rel: str, size_kind: str, out_dirs: tuple[Path, ...]) -> Path:
    """公開してはいけない項目とサイズ上限を確かめてから、一時ファイル経由で書く。"""
    errors = check_forbidden(obj)
    if errors:
        raise SchemaError("analytics", errors)
    text = dumps(obj)
    ar.check_size(size_kind, len(gzip.compress(text.encode("utf-8"))))
    first = None
    for base in out_dirs:
        from src.export.io_util import write_json

        path = Path(base) / SUBDIR / rel
        write_json(path, obj)
        first = first or path
    return first


def _rename_notes(v: Any) -> Any:
    """モデル担当の出力の説明文 "note" / "notes" は、個人のメモ(公開禁止のキー)と同じ名前なので
    "explanation" / "explanations" にする(どの階層でも)。"""
    if isinstance(v, dict):
        ren = {"note": "explanation", "notes": "explanations"}
        return {ren.get(k, k): _rename_notes(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_rename_notes(x) for x in v]
    return v


def _envelope(kind: str, generated_at: str, **body: Any) -> dict[str, Any]:
    body = _rename_notes(body)
    return {"schema_version": SCHEMA_VERSION, "kind": kind, "generated_at": generated_at, **body}


def _name(sn: int) -> str:
    return "全国" if sn == 0 else codes.STADIUM_NAMES.get(sn, str(sn))


# ---------------------------------------------------------------------------
# A1 場の傾向
# ---------------------------------------------------------------------------

def build_stadiums(course: pd.DataFrame, condition: pd.DataFrame, summary: pd.DataFrame,
                   generated_at: str) -> dict[str, Any]:
    stadiums: dict[str, Any] = {}
    periods: dict[str, Any] = {}
    for s in summary.to_dict("records"):
        sn, period = int(s["stadium_number"]), str(s["period"])
        if sn == 0:
            periods[period] = {"from": clean(s.get("date_from")), "to": clean(s.get("date_to"))}
        st = stadiums.setdefault(str(sn), {"name": _name(sn)})
        summ = records(pd.DataFrame([s]), "n_races", drop=("stadium_number", "period", "date_from", "date_to"))[0]
        c = course[(course["stadium_number"] == sn) & (course["period"] == period)].sort_values("course")
        conds: dict[str, list] = {}
        g = condition[(condition["stadium_number"] == sn) & (condition["period"] == period)]
        for ct, gg in g.groupby("cond_type", sort=True):
            conds[str(ct)] = records(gg, "n_races", drop=("stadium_number", "period", "cond_type"))
        st[period] = {"summary": summ, "course": records(c, "n", drop=("stadium_number", "period")),
                      "conditions": conds}
    return _envelope("stadiums", generated_at, periods=periods,
                     stadiums=dict(sorted(stadiums.items(), key=lambda kv: int(kv[0]))))


def export_stadiums(out_dirs, generated_at, in_dir: Path = ANALYTICS_IN) -> Path:
    d = Path(in_dir) / "stadiums"
    obj = build_stadiums(pd.read_parquet(d / "course.parquet"), pd.read_parquet(d / "condition.parquet"),
                         pd.read_parquet(d / "summary.parquet"), generated_at)
    return write_analytics(obj, "stadiums.json", "stadiums", out_dirs)


# ---------------------------------------------------------------------------
# A2 条件で集計(キューブ。場ごと)
# ---------------------------------------------------------------------------

def build_cube(sn: int, df: pd.DataFrame, generated_at: str) -> dict[str, Any]:
    rows = records(df, "n_starts")
    return _envelope("cube", generated_at, stadium_number=sn, stadium_name=_name(sn),
                     periods=sorted({r["period"] for r in rows}), rows=rows)


def export_cube(out_dirs, generated_at, in_dir: Path = ANALYTICS_IN) -> list[Path]:
    files = sorted((Path(in_dir) / "cube").glob("*.parquet"))
    if not files:
        raise AnalyticsError("キューブの入力がありません(data/features/analytics/cube/)")
    objs = [(int(f.stem), build_cube(int(f.stem), pd.read_parquet(f), generated_at)) for f in files]
    total = sum(len(gzip.compress(dumps(o).encode())) for _, o in objs)
    ar.check_size("cube_total", total)
    return [write_analytics(o, f"cube/{sn:02d}.json", "cube_per_stadium", out_dirs) for sn, o in objs]


# ---------------------------------------------------------------------------
# A3 ランキング(当日。2日分だけ保管)
# ---------------------------------------------------------------------------

def build_rankings(d: Date, df: pd.DataFrame, generated_at: str) -> dict[str, Any]:
    """件数が下限(RANKING_MIN_COUNT。既定30)未満の行は出さない。motor_number は公開の禁止キーなので motor にする。"""
    out: dict[str, dict[str, list]] = {}
    for r in df.sort_values(["ranking", "scope", "rank"]).to_dict("records"):
        n = clean(r.get("n"))
        minimum = RANKING_MIN_COUNT.get(str(r["ranking"]), ar.MIN_COUNT_SHOW)
        if n is not None and n < minimum:
            continue
        row = {
            "rank": int(r["rank"]),
            "racer_number": clean(r.get("racer_number")), "racer_name": clean(r.get("racer_name")),
            "stadium": int(r["stadium_number"]), "race": int(r["race_number"]), "entry": int(r["entry_number"]),
            "motor": clean(r.get("motor_number")),
            "value": clean(r.get("value")), "n": int(n) if n is not None else None,
            "value2": clean(r.get("value2")), "n2": int(clean(r["n2"])) if clean(r.get("n2")) is not None else None,
        }
        out.setdefault(str(r["ranking"]), {}).setdefault(str(int(r["scope"])), []).append(row)
    return _envelope("rankings", generated_at, date=d.isoformat(), scope_note="scope 0 は全場、それ以外は場番号",
                     rankings=out)


def export_rankings(d: Date, out_dirs, generated_at, in_dir: Path = ANALYTICS_IN) -> Path:
    p = Path(in_dir) / "rankings" / f"{d:%Y%m%d}.parquet"
    if not p.exists():
        raise AnalyticsError(f"ランキングの入力がありません: {p}")
    path = write_analytics(build_rankings(d, pd.read_parquet(p), generated_at), f"rankings/{d:%Y%m%d}.json",
                           "rankings", out_dirs)
    keep = {f"{d - timedelta(days=i):%Y%m%d}.json" for i in range(RANKING_DAYS_KEPT)}
    for base in out_dirs:
        for old in (Path(base) / SUBDIR / "rankings").glob("*.json"):
            if old.name not in keep and old.name < f"{d:%Y%m%d}.json":
                old.unlink()
    return path


# ---------------------------------------------------------------------------
# A4・A6・A7(モデル担当の関数の結果を包むだけ)
# ---------------------------------------------------------------------------

def export_model(d_from, d_to, out_dirs, generated_at, builder: Callable | None = None) -> Path:
    if builder is None:
        from src.model.analytics_eval import build_model_weakness as builder
    body = clean(builder(d_from, d_to))
    return write_analytics(_envelope("model_weakness", generated_at, **body), "model_weakness.json",
                           "model_weakness", out_dirs)


def export_weekly(week: str, out_dirs, generated_at, builder: Callable | None = None) -> Path:
    if builder is None:
        from src.model.analytics_eval import build_weekly as builder
    body = clean(builder(week))
    return write_analytics(_envelope("weekly", generated_at, **body), f"weekly/{week}.json", "weekly", out_dirs)


def export_simulator(live_from, live_to, out_dirs, generated_at, builder: Callable | None = None) -> Path:
    if builder is None:
        from src.model.simulator import build_simulator as builder
    body = clean(builder(live_from, live_to))
    return write_analytics(_envelope("simulator", generated_at, **body), "simulator.json", "simulator", out_dirs)


# ---------------------------------------------------------------------------
# index.json
# ---------------------------------------------------------------------------

def build_index(base: Path, generated_at: str) -> dict[str, Any]:
    root = Path(base) / SUBDIR
    files: dict[str, Any] = {}

    def meta(p: Path) -> dict[str, Any]:
        try:
            o = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        m = {"file": str(p.relative_to(root)), "generated_at": o.get("generated_at")}
        for k in ("period", "periods", "date", "from", "to"):
            if k in o:
                m[k] = o[k]
        return m

    for name in ("stadiums", "model_weakness", "simulator"):
        p = root / f"{name}.json"
        if p.exists():
            files[name] = meta(p)
    cube = sorted((root / "cube").glob("*.json"))
    if cube:
        files["cube"] = {"files": [str(p.relative_to(root)) for p in cube],
                         "generated_at": max((meta(p).get("generated_at") or "") for p in cube)}
    files["rankings"] = [p.stem for p in sorted((root / "rankings").glob("*.json"), reverse=True)]
    files["weekly"] = [p.stem for p in sorted((root / "weekly").glob("*.json"), reverse=True)]
    return {"schema_version": SCHEMA_VERSION, "kind": "index", "generated_at": generated_at, "files": files}


def write_index(out_dirs, generated_at) -> None:
    for base in out_dirs:
        if (Path(base) / SUBDIR).exists():
            write_analytics(build_index(base, generated_at), "index.json", "index", (base,))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def previous_iso_week(d: Date) -> str:
    y, w, _ = (d - timedelta(days=7)).isocalendar()
    return f"{y}-W{w:02d}"


def run(what: str, d: Date | None, week: str | None, d_from: str | None, d_to: str | None,
        out_dirs: tuple[Path, ...], generated_at: str | None = None) -> list[Path]:
    generated_at = generated_at or to_jst_iso(datetime.now(JST))
    d = d or datetime.now(JST).date()
    live_from = d_from or "2026-11-02"           # 実運用の開始日(確定版がある最初の日)
    live_to = d_to or (d - timedelta(days=1)).isoformat()
    done: list[Path] = []
    if what in ("stadiums", "all"):
        done.append(export_stadiums(out_dirs, generated_at))
    if what in ("rankings", "all"):
        done.append(export_rankings(d, out_dirs, generated_at))
    if what in ("cube", "all"):
        done += export_cube(out_dirs, generated_at)
    if what in ("model", "all"):
        done.append(export_model(live_from, live_to, out_dirs, generated_at))
    if what in ("weekly", "all"):
        done.append(export_weekly(week or previous_iso_week(d), out_dirs, generated_at))
    if what in ("simulator", "all"):
        done.append(export_simulator(live_from, live_to, out_dirs, generated_at))
    write_index(out_dirs, generated_at)
    return done


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="分析用の公開JSON(spec 11)")
    ap.add_argument("--what", required=True,
                    choices=("stadiums", "rankings", "cube", "model", "weekly", "simulator", "all"))
    ap.add_argument("--date", default=None, help="YYYY-MM-DD(rankings の日、既定は今日)")
    ap.add_argument("--week", default=None, help="YYYY-Www(既定は前の週)")
    ap.add_argument("--from", dest="d_from", default=None, help="実運用の集計の開始日")
    ap.add_argument("--to", dest="d_to", default=None, help="実運用の集計の終了日(既定は前日)")
    ap.add_argument("--sample", action="store_true", help="docs/data/sample(-local)/ に書く")
    ap.add_argument("--generated-at", default=None)
    args = ap.parse_args(argv)
    out_dirs = (DOCS_DATA_DIR / "sample", DOCS_DATA_DIR / "sample-local") if args.sample \
        else (DOCS_DATA_DIR, LOCAL_DATA_DIR)
    d = Date.fromisoformat(args.date) if args.date else None
    for p in run(args.what, d, args.week, args.d_from, args.d_to, out_dirs, args.generated_at):
        print(f"書き出し: {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
