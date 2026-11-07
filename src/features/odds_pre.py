"""締切前オッズの記録(Worker)→ interim の odds_pre 表(F-13b。spec/13 4章)。**公開しない**。

入力: data/raw/odds_record/YYYYMMDD/SS-RR.json(.json.gz も可)。CI の前日確定ジョブが R2 の
      odds/YYYYMMDD/ から取ってきたもの。形(workers/beforeinfo/src/odds.mjs):
      {race_id, closed_at,
       pages: {tf: {status, fetched_at, minutes_before_close, odds_time_label, is_final_odds} | "error" など, 2tf: {...}},
       odds: {win, place, exacta, quinella[, trifecta]}}   # odds の入れ子は turnmark と同じ形
出力: data/interim/odds_pre/YYYY-MM.parquet(1行 = 1レース × 券種 × 組合せ)
      race_id, bet_type, combination, odds, odds_lower, odds_upper(複勝・拡連複は下限・上限),
      fetched_at(取得時刻 = オッズの時点), minutes_before_close, is_final_odds, odds_time_label, source(worker_record)
- 券種の時刻・状態は、その券種のページ(tf: 単勝・複勝、2tf: 2連単・2連複、3t: 3連単)の値。
  ページの取得が失敗した券種(pages[種類] が文字列)は行を作らない。
- 欠場艇の組合せ・0.0 は null(解析側で null になっている)。行は残す。
- 確定オッズの odds 表(turnmark)とは別の表。**数値を公開JSON・ログに出さない**。
"""

from __future__ import annotations

import gzip
import json
from collections.abc import Callable
from datetime import date
from pathlib import Path

import pandas as pd

from src.collect import raw_io
from src.features import parse

SOURCE = "worker_record"
PAGE_OF_BET = {"win": "tf", "place": "tf", "exacta": "2tf", "quinella": "2tf", "trifecta": "3t"}

ODDS_PRE_DTYPES: dict[str, str] = {
    "race_id": "string",
    "bet_type": "string",
    "combination": "string",
    "odds": "float32",
    "odds_lower": "float32",
    "odds_upper": "float32",
    "fetched_at": "object",            # tz 付き datetime(Asia/Tokyo)に変換する
    "minutes_before_close": "float32",
    "is_final_odds": "boolean",
    "odds_time_label": "string",
    "source": "string",
}

LoadOddsRecord = Callable[[date], dict]


def make_odds_record_loader(raw_dir: Path | None = None) -> LoadOddsRecord:
    """1日分の記録を {race_id: dict} で返す関数を作る。無ければ空の dict。"""
    base = (Path(raw_dir) if raw_dir is not None else raw_io.RAW_DIR) / "odds_record"

    def _load(target: date) -> dict:
        day = base / target.strftime("%Y%m%d")
        if not day.is_dir():
            return {}
        out: dict[str, dict] = {}
        for path in sorted(day.glob("*.json*")):
            if path.name.startswith("_") or path.name.endswith(".tmp"):
                continue  # _state.json・_stopped.json など
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


def records_to_rows(records: dict[str, dict]) -> pd.DataFrame:
    """記録(1日分)を odds_pre の行にする。"""
    rows: list[dict] = []
    for rid, data in records.items():
        pages = data.get("pages") or {}
        odds = data.get("odds") or {}
        for r in parse._odds_rows(rid, odds):
            page = pages.get(PAGE_OF_BET.get(r["bet_type"], ""))
            if not isinstance(page, dict) or page.get("status") not in (None, "ok"):
                continue
            r.update({
                "fetched_at": page.get("fetched_at"),
                "minutes_before_close": page.get("minutes_before_close"),
                "is_final_odds": page.get("is_final_odds"),
                "odds_time_label": page.get("odds_time_label"),
                "source": SOURCE,
            })
            rows.append(r)
    return cast_odds_pre(pd.DataFrame(rows, columns=list(ODDS_PRE_DTYPES)))


def cast_odds_pre(df: pd.DataFrame) -> pd.DataFrame:
    df = df.reindex(columns=list(ODDS_PRE_DTYPES)).copy()
    for c, dt in ODDS_PRE_DTYPES.items():
        if c == "fetched_at":
            s = pd.to_datetime(df[c], errors="coerce", utc=True, format="ISO8601")
            df[c] = s.dt.tz_convert(parse.TZ)
        elif dt == "float32":
            df[c] = pd.to_numeric(df[c], errors="coerce").astype(dt)
        else:
            df[c] = df[c].astype(dt)
    return df


def win_popularity(odds_pre: pd.DataFrame) -> pd.DataFrame:
    """締切前オッズの単勝から、各艇の人気順を求める(モデル担当向け)。

    戻り値(1行 = 1レース × 艇): race_id, entry_number, win_odds, popularity(1 = 1番人気。
    同じオッズは同じ順位)、is_favorite(1番人気か)、fetched_at, minutes_before_close。
    欠場などでオッズが null の艇は popularity も null。
    """
    w = odds_pre[odds_pre["bet_type"] == "win"].copy()
    out = pd.DataFrame({
        "race_id": w["race_id"].values,
        "entry_number": pd.to_numeric(w["combination"], errors="coerce").astype("Int8").values,
        "win_odds": w["odds"].astype("float32").values,
        "fetched_at": w["fetched_at"].array,
        "minutes_before_close": w["minutes_before_close"].values,
    })
    out["popularity"] = out.groupby("race_id")["win_odds"].rank(method="min").astype("Int8")
    out["is_favorite"] = (out["popularity"] == 1).astype("boolean").where(out["popularity"].notna(), pd.NA)
    return (out.sort_values(["race_id", "entry_number"]).reset_index(drop=True)
            [["race_id", "entry_number", "win_odds", "popularity", "is_favorite", "fetched_at",
              "minutes_before_close"]])
