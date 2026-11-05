"""A3 選手・モーターのランキング(F-13。spec/11 2.3)。対象は D 日の出走表に載っている選手・モーター。

値はすべて D−1 までの結果から作る(学習用と同じ特徴量の関数 make_feature_frame(pre_v2)を通す。
履歴の状態 F-8 があれば状態から作る)。

出力: data/features/analytics/rankings/YYYYMMDD.parquet(1行=1件。上位10件ずつ)
  列: ranking, scope(0=全場、それ以外は場番号), rank, racer_number, racer_name, stadium_number,
      race_number, entry_number, motor_number, value, n, value2, n2
  ranking:
    nige        今日コース1(枠番1)の選手の、コース1での逃げ率(縮小後)。n=コース1の出走数
    avg_st      平均ST(全コース。本番のST)。小さい順。n=STのある出走数
    sashi       今日コース2〜4の選手の、そのコースでの差し率。n=そのコースの出走数
    makuri      今日コース2〜4の選手の、そのコースでの まくり+まくり差し の率
    motor       場ごと。出走表のモーター2連率(value)と、今節(D−1まで)の2連対率(value2、n2=今節の出走数)
    series      場ごと。今節(D−1まで)の平均着順(小さい順。失格は7)。n=今節の出走数
  件数の下限: nige / avg_st / sashi / makuri は件数30未満を対象外(共通ルール MIN_COUNT_SHOW)。
    series は1節が数走しかないため、下限を SERIES_MIN_STARTS(2走)にしている(spec と違う。PM に報告済み)。
    motor の順位は出走表の値(公式)で付けるので件数の下限は無い。
  予定コースは前日版(枠番)。同じ選手が複数レースに出る場合は、条件に当たる最初のレースで1件にする。

使い方:
    python -m src.features.analytics.rankings --date 2026-11-06 [--state data/history/state_20261106.parquet]
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from src.common import analytics_rules as ar
from src.features import parse
from src.features.analytics.base import ANALYTICS_DIR, write_parquet
from src.features.build_features import make_feature_frame
from src.features.build_interim import INTERIM_DIR, read_interim
from src.features.history import assign_series, day_ordinal, make_starts, window_sum
from src.features.raw_source import LoadRaw, load_day, make_loader
from src.features.style import make_style_starts

TOP_N = 10
SERIES_MIN_STARTS = 2
NATIONAL = 0
COLUMNS = ["ranking", "scope", "rank", "racer_number", "racer_name", "stadium_number", "race_number",
           "entry_number", "motor_number", "value", "n", "value2", "n2"]


def _day_frame(target: date, day_entries: pd.DataFrame, day_races: pd.DataFrame,
               hist_entries, hist_races, state) -> pd.DataFrame:
    """当日の艇ごとの値(特徴量 + ランキング用の件数)。"""
    f = make_feature_frame(day_entries, day_races, hist_entries, hist_races, "pre_v2",
                           with_target=False, state=state)
    e = day_entries[["race_id", "entry_number", "racer_name", "motor_number"]]
    f = f.merge(e, on=["race_id", "entry_number"], how="left")
    # 件数(平均ST、今節の出走数)は特徴量に無いので、同じ材料の表から D より前の合計を取る
    if state is not None:
        h_starts, st_starts, races = state.h_starts, state.style_starts, state.races
    else:
        he, hr = hist_entries[hist_entries["date"] < target], hist_races[hist_races["date"] < target]
        races = hr[["stadium_number", "date", "title"]]
        h_starts = make_starts(he, assign_series(races))
        st_starts = make_style_starts(he, hr)
    smap = assign_series(pd.concat([races[["stadium_number", "date", "title"]],
                                    day_races[["stadium_number", "date", "title"]]], ignore_index=True))
    sid = f[["stadium_number", "date"]].astype({"stadium_number": "int64"}).merge(
        smap, on=["stadium_number", "date"], how="left")["series_id"]
    q = pd.DataFrame({"dn": day_ordinal(f["date"]), "racer_number": f["racer_number"].astype("Float64").values,
                      "motor_number": f["motor_number"].astype("Float64").values,
                      "series_id": sid.astype("Float64").values}, index=f.index)
    st = window_sum(st_starts, ["racer_number"], ["st", "st_n"], q, None)
    f["avg_st"] = (st["st"] / st["st_n"].where(st["st_n"] > 0)).astype(float)
    f["st_n"] = st["st_n"]
    rs = window_sum(h_starts, ["series_id", "racer_number"], ["fin_n"], q, None)
    f["series_n"] = rs["fin_n"]
    ms = window_sum(h_starts, ["series_id", "motor_number"], ["n"], q, None)
    f["motor_series_n"] = ms["n"]
    return f


def _top(df: pd.DataFrame, ranking: str, value: str, n: str | None, ascending: bool, min_n: int | None,
         per_stadium: bool, national: bool, value2: str | None = None, n2: str | None = None) -> pd.DataFrame:
    d = df[df[value].notna()].copy()
    if n is not None and min_n is not None:
        d = d[d[n] >= min_n]
    key = "motor_key" if ranking == "motor" else "racer_number"
    d = d.sort_values(["race_number", "entry_number"]).drop_duplicates(key)
    out = []
    scopes = ([NATIONAL] if national else []) + (sorted(d["stadium_number"].unique()) if per_stadium else [])
    for sc in scopes:
        g = d if sc == NATIONAL else d[d["stadium_number"] == sc]
        g = g.sort_values([value, "racer_number"], ascending=[ascending, True]).head(TOP_N)
        out.append(pd.DataFrame({
            "ranking": ranking, "scope": int(sc), "rank": np.arange(1, len(g) + 1),
            "racer_number": g["racer_number"].values, "racer_name": g["racer_name"].values,
            "stadium_number": g["stadium_number"].values, "race_number": g["race_number"].values,
            "entry_number": g["entry_number"].values, "motor_number": g["motor_number"].values,
            "value": g[value].values, "n": g[n].values if n else np.nan,
            "value2": g[value2].values if value2 else np.nan, "n2": g[n2].values if n2 else np.nan,
        }))
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame(columns=COLUMNS)


def build_rankings(f: pd.DataFrame) -> pd.DataFrame:
    m = ar.MIN_COUNT_SHOW
    c = f["entry_number"]
    in24 = f[c.between(2, 4)].copy()
    in24["makuri_all"] = in24["h_cs_makuri_all"] + in24["h_cs_makurisashi_all"]
    motors = f.assign(motor_key=f["stadium_number"].astype(int) * 1000 + f["motor_number"].astype("Float64").fillna(-1).astype(int))
    motors = motors[motors["motor_number"].notna()]
    parts = [
        _top(f[c == 1], "nige", "h_cs_nige_all", "h_cs_n_all", False, m, True, True),
        _top(f, "avg_st", "avg_st", "st_n", True, m, True, True),
        _top(in24, "sashi", "h_cs_sashi_all", "h_cs_n_all", False, m, True, True),
        _top(in24, "makuri", "makuri_all", "h_cs_n_all", False, m, True, True),
        _top(motors, "motor", "motor_top_2_percent", None, False, None, True, False,
             value2="h_motor_series_top2", n2="motor_series_n"),
        _top(f, "series", "h_racer_series_avg_finish", "series_n", True, SERIES_MIN_STARTS, True, False),
    ]
    out = pd.concat(parts, ignore_index=True)[COLUMNS]
    for col in ("value", "n", "value2", "n2"):
        out[col] = out[col].astype("float64")
    for col in ("scope", "rank", "stadium_number", "race_number", "entry_number"):
        out[col] = out[col].astype("int64")
    out["racer_number"] = out["racer_number"].astype("Int64")
    out["motor_number"] = out["motor_number"].astype("Float64").astype("Int64")
    out["ranking"] = out["ranking"].astype("string")
    return out


def build_day(target: date, interim_dir: Path = INTERIM_DIR, out_dir: Path = ANALYTICS_DIR,
              load_raw: LoadRaw | None = None, state=None) -> pd.DataFrame:
    loaded = load_day(target, load_raw or make_loader(), sources=("openapi", "turnmark"))
    if loaded is None:
        raise FileNotFoundError(f"{target} の生データがありません")
    source, raw = loaded
    t = parse.parse_day(raw, source, target)
    if state is not None:
        from src.features.history_state import load_state
        if not hasattr(state, "h_starts"):
            state = load_state(Path(state))
        if state.as_of != target:
            raise ValueError(f"状態の日付 {state.as_of} が対象日 {target} と違います")
        he = hr = None
    else:
        prev = target - timedelta(days=1)
        he, hr = read_interim("entries", None, prev, interim_dir), read_interim("races", None, prev, interim_dir)
    f = _day_frame(target, t["entries"], t["races"], he, hr, state)
    out = build_rankings(f)
    write_parquet(out, out_dir / "rankings" / f"{target.strftime('%Y%m%d')}.parquet")
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="A3 ランキング(当日の出走表)")
    p.add_argument("--date", required=True)
    p.add_argument("--state")
    a = p.parse_args(argv)
    out = build_day(date.fromisoformat(a.date), state=a.state)
    print(f"{a.date}: " + " ".join(f"{k}={v}" for k, v in out["ranking"].value_counts().items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
