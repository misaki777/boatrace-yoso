"""生JSON → interim(races / entries / payouts / odds)の parquet と品質レポート。

使い方:
    python -m src.features.build_interim --from 2026-01-01 --to 2026-11-01

- 出力は月ごと: data/interim/{table}/YYYY-MM.parquet
- 指定範囲にかかる月は、月の初日〜末日を丸ごと読み直して作り直す(冪等)。
- 品質レポート: data/interim/_quality/YYYY-MM.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from src.features import parse
from src.features.raw_source import LoadRaw, load_day, make_final_checker, make_loader

ROOT = Path(__file__).resolve().parents[2]
INTERIM_DIR = ROOT / "data" / "interim"
TABLES = ("races", "entries", "payouts", "odds")
TABLE_DTYPES = {
    "races": parse.RACES_DTYPES,
    "entries": parse.ENTRIES_DTYPES,
    "payouts": parse.PAYOUTS_DTYPES,
    "odds": parse.ODDS_DTYPES,
}
JST = timezone(timedelta(hours=9))


# ---------------------------------------------------------------------------
# 月の扱い
# ---------------------------------------------------------------------------


def month_key(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def month_range(key: str) -> tuple[date, date]:
    y, m = map(int, key.split("-"))
    first = date(y, m, 1)
    nxt = date(y + 1, 1, 1) if m == 12 else date(y, m + 1, 1)
    return first, nxt - timedelta(days=1)


def months_between(d_from: date, d_to: date) -> list[str]:
    keys: list[str] = []
    d = date(d_from.year, d_from.month, 1)
    while d <= d_to:
        keys.append(month_key(d))
        d = date(d.year + 1, 1, 1) if d.month == 12 else date(d.year, d.month + 1, 1)
    return keys


def iter_dates(d_from: date, d_to: date):
    d = d_from
    while d <= d_to:
        yield d
        d += timedelta(days=1)


# ---------------------------------------------------------------------------
# 本体
# ---------------------------------------------------------------------------


def build_month(key: str, load_raw: LoadRaw, until: date | None = None,
                is_final=None) -> tuple[dict[str, pd.DataFrame], dict]:
    """1か月分の interim と品質レポートを作る(書き込みはしない)。

    is_final: (source, date) -> bool。None なら turnmark=確定、openapi=未確定。
    """
    first, last = month_range(key)
    if until is not None:
        last = min(last, until)
    parts: dict[str, list[pd.DataFrame]] = {t: [] for t in TABLES}
    day_sources: dict[str, str] = {}
    missing_days: list[str] = []
    errors: list[str] = []
    for d in iter_dates(first, last):
        loaded = load_day(d, load_raw)
        if loaded is None:
            missing_days.append(d.isoformat())
            continue
        source, raw = loaded
        try:
            final = is_final(source, d) if is_final is not None else None
            tables = parse.parse_day(raw, source, d, is_final=final)
        except Exception as e:  # 1日分の異常で全体を止めない。レポートに残す
            errors.append(f"{d.isoformat()}: {type(e).__name__}: {e}")
            continue
        day_sources[d.isoformat()] = source
        for t in TABLES:
            if len(tables[t]):
                parts[t].append(tables[t])

    out: dict[str, pd.DataFrame] = {}
    for t in TABLES:
        if parts[t]:
            df = pd.concat(parts[t], ignore_index=True)
        else:
            df = parse.cast_frame(pd.DataFrame(columns=list(TABLE_DTYPES[t])), TABLE_DTYPES[t])
        out[t] = parse.cast_frame(df, TABLE_DTYPES[t])

    report = quality_report(key, out, day_sources, missing_days, errors)
    return out, report


def quality_report(key: str, t: dict[str, pd.DataFrame], day_sources: dict[str, str],
                   missing_days: list[str], errors: list[str]) -> dict:
    """件数中心の品質レポート(02_features.md 1.5節)。"""
    races, entries, payouts, odds = t["races"], t["entries"], t["payouts"], t["odds"]
    has_result = (
        entries["place_number"].notna().groupby(entries["race_id"]).any()
        if len(entries) else pd.Series(dtype=bool)
    )
    n_win = (
        entries.assign(w=entries["finish_position"].eq(1).fillna(False))
        .groupby("race_id")["w"].sum()
        if len(entries) else pd.Series(dtype=int)
    )
    races_with_result = int(has_result.sum())
    null_rate = {
        c: round(float(entries[c].isna().mean()), 4)
        for c in entries.columns if len(entries) and entries[c].isna().any()
    }
    # 結果のあるレースに限った欠損率(締切前のレースによる null を除いて見るため)
    valid_ids = set(races.loc[races["is_valid_result"].fillna(False), "race_id"])
    ev = entries[entries["race_id"].isin(valid_ids)]
    null_rate_valid = {
        c: round(float(ev[c].isna().mean()), 4)
        for c in ev.columns if len(ev) and ev[c].isna().any()
    }
    odds_races = odds["race_id"].nunique() if len(odds) else 0
    return {
        "month": key,
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "days": {
            "with_data": len(day_sources),
            "by_source": pd.Series(day_sources, dtype="object").value_counts().to_dict(),
            "missing": missing_days,
            "parse_errors": errors,
        },
        "races": {
            "total": int(len(races)),
            "with_result": races_with_result,
            "without_result": int(len(races) - races_with_result),
            "valid": int(races["is_valid_result"].fillna(False).sum()),
            "canceled": int(races["is_canceled"].fillna(False).sum()),
            "canceled_unknown": int(races["is_canceled"].isna().sum()),
            "canceled_or_postponed_day": int((races["day_status"] != "normal").sum()),
            "incomplete_entries": int((races["n_entries"] != 6).sum()),
            "dead_heat": int(races["has_dead_heat"].fillna(False).sum()),
            "dead_heat_first": int((n_win > 1).sum()),
            "with_refund": int(races["has_refund"].fillna(False).sum()),
            "with_odds": int(odds_races),
            "turnmark_without_odds": int(
                len(set(races.loc[races["source"] == "turnmark", "race_id"])
                    - set(odds["race_id"]))
            ),
        },
        "entries": {
            "total": int(len(entries)),
            "absent": int(entries["is_absent"].fillna(False).sum()),
            "disqualified": int(entries["is_disqualified"].fillna(False).sum()),
            "null_rate": null_rate,
            "null_rate_valid_races": null_rate_valid,
        },
        "payouts": {
            "rows": int(len(payouts)),
            "null_combination": int(payouts["combination"].isna().sum()),
            "by_bet_type": payouts["bet_type"].value_counts().to_dict(),
        },
        "odds": {
            "rows": int(len(odds)),
            "by_bet_type": odds["bet_type"].value_counts().to_dict(),
            "null_value": int((odds["odds"].isna() & odds["odds_lower"].isna()).sum()),
        },
    }


def write_month(key: str, tables: dict[str, pd.DataFrame], report: dict,
                out_dir: Path = INTERIM_DIR) -> None:
    for t, df in tables.items():
        path = out_dir / t / f"{key}.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        if len(df) == 0:
            # 空の月はファイルを残さない(古いファイルがあれば消す)
            path.unlink(missing_ok=True)
            continue
        tmp = path.with_name(path.name + ".tmp")
        df.to_parquet(tmp, index=False)
        tmp.replace(path)
    qpath = out_dir / "_quality" / f"{key}.json"
    qpath.parent.mkdir(parents=True, exist_ok=True)
    qpath.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def build_interim(d_from: date, d_to: date, load_raw: LoadRaw | None = None,
                  out_dir: Path = INTERIM_DIR, verbose: bool = True,
                  is_final=None) -> list[dict]:
    """指定範囲にかかる月を作り直す。月ごとの品質レポートのリストを返す。

    load_raw を省略したときは data/raw と、その取得ログで確定判定をする。
    """
    if load_raw is None:
        load_raw = make_loader()
        is_final = is_final or make_final_checker()
    # 未来の日は読まない(品質レポートの「欠けた日」に入れないため)
    until = max(d_to, datetime.now(JST).date())
    reports = []
    for key in months_between(d_from, d_to):
        tables, report = build_month(key, load_raw, until=until, is_final=is_final)
        write_month(key, tables, report, out_dir)
        reports.append(report)
        if verbose:
            r = report["races"]
            print(f"{key}: 日数={report['days']['with_data']} レース={r['total']} "
                  f"有効={r['valid']} 艇={report['entries']['total']} "
                  f"払戻={report['payouts']['rows']} オッズ={report['odds']['rows']}")
    return reports


# ---------------------------------------------------------------------------
# 読み込み(特徴量・モデル・エクスポート側から使う)
# ---------------------------------------------------------------------------


def read_interim(table: str, d_from: date | None = None, d_to: date | None = None,
                 interim_dir: Path = INTERIM_DIR, columns: list[str] | None = None) -> pd.DataFrame:
    """interim の1テーブルを読む。日付範囲で月ファイルを絞る(両端を含む)。"""
    files = sorted((interim_dir / table).glob("*.parquet"))
    if d_from is not None or d_to is not None:
        keep = []
        for f in files:
            first, last = month_range(f.stem)
            if (d_from is None or last >= d_from) and (d_to is None or first <= d_to):
                keep.append(f)
        files = keep
    dtypes = TABLE_DTYPES[table]
    if not files:
        return parse.cast_frame(pd.DataFrame(columns=list(dtypes)), dtypes)
    df = pd.concat([pd.read_parquet(f, columns=columns) for f in files], ignore_index=True)
    if columns is None:
        df = parse.cast_frame(df, dtypes)
    if "date" in df.columns and (d_from is not None or d_to is not None):
        mask = pd.Series(True, index=df.index)
        if d_from is not None:
            mask &= df["date"] >= d_from
        if d_to is not None:
            mask &= df["date"] <= d_to
        df = df[mask].reset_index(drop=True)
    elif table in ("payouts", "odds") and (d_from is not None or d_to is not None):
        ymd = df["race_id"].str.slice(0, 8)
        if d_from is not None:
            df = df[ymd >= d_from.strftime("%Y%m%d")]
        if d_to is not None:
            df = df[ymd <= d_to.strftime("%Y%m%d")]
        df = df.reset_index(drop=True)
    return df


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="生JSONから interim の parquet を作る")
    p.add_argument("--from", dest="d_from", required=True, help="開始日 YYYY-MM-DD")
    p.add_argument("--to", dest="d_to", required=True, help="終了日 YYYY-MM-DD(含む)")
    args = p.parse_args(argv)
    try:
        d_from = date.fromisoformat(args.d_from)
        d_to = date.fromisoformat(args.d_to)
    except ValueError as e:
        print(f"日付の形式が不正です: {e}", file=sys.stderr)
        return 2
    if d_from > d_to:
        print("--from が --to より後です", file=sys.stderr)
        return 2
    build_interim(d_from, d_to)
    return 0


if __name__ == "__main__":
    sys.exit(main())
