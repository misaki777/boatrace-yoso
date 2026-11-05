"""選手カードの集計(F-10。spec/10 4.1節)。E-14 が docs/data/racers/YYYYMMDD.json に出力する。

対象日 D に出走する選手について、D−1 までの結果だけで集計する。出力は2つの parquet:

- data/features/racer_cards/YYYYMMDD_cards.parquet(1行=1選手)
    基本: racer_number, racer_name, rank_number, branch_number, branch_name(短い名前)
    出走表: national_win_rate ほか、local_*、average_start_timing, flying_count, late_count
      (当日の出走表の値。同じ日に複数レースに出ていれば、R番号の小さいレースの値)
    当地の自前集計(この場で直近365日): local365_n, local365_win_rate, local365_top2_rate, local365_top3_rate
    コース別 c{k}_*(k=1〜6): n, win_rate, nige, sashi, makuri, makurisashi, second, third, st_mean
      (style 集計の累計の値。率は全体平均への縮小 (k+10・p)/(n+10) 済み。件数 n も出すので、
       アプリでは件数の少ない率を薄く表示する)
    選手単位: f_rate, late_rate, maeduke_rate(累計。縮小済み)
- data/features/racer_cards/YYYYMMDD_results.parquet(1行=1出走。kind で区別)
    kind="series": 今節(同じ場・同じ節)の前日までの全レース
    kind="recent": 直近10走(D−1 まで。欠場は除く)
    列: racer_number, kind, date, stadium_number, race_number, course_number, place_number,
        finish_position, technique_number(1着のときだけ。それ以外は null)

年齢・体重・出身地・モーター番号ごとの履歴は含めない(公開しない項目。テストで確認)。

使い方:
    python -m src.features.racer_cards --date 2026-11-06 [--state data/history/state_20261106.parquet]
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from src.common import codes
from src.features import parse
from src.features.build_features import FEATURES_DIR, _write_parquet
from src.features.build_interim import INTERIM_DIR, read_interim
from src.features.history import assign_series
from src.features.raw_source import LoadRaw, load_day, make_loader
from src.features.style import compute_style_h

CARDS_DIR = FEATURES_DIR / "racer_cards"
RECENT_N = 10
LOCAL_DAYS = 365
# 結果の一覧と当地集計のために読む過去の日数(状態を使うときの interim も、この範囲があればよい)
RESULT_LOOKBACK_DAYS = 400

PROGRAM_COLUMNS = [
    "national_win_rate", "national_top_2_percent", "national_top_3_percent",
    "local_win_rate", "local_top_2_percent", "local_top_3_percent",
    "average_start_timing", "flying_count", "late_count",
]
COURSE_STATS = {  # カードの名前 → style の列(累計)
    "n": "h_cs_n_all",
    "nige": "h_cs_nige_all",
    "sashi": "h_cs_sashi_all",
    "makuri": "h_cs_makuri_all",
    "makurisashi": "h_cs_makurisashi_all",
    "second": "h_cs_second_all",
    "third": "h_cs_third_all",
    "st_mean": "h_cs_st_mean_all",
}
WIN_PARTS = ["h_cs_nige_all", "h_cs_sashi_all", "h_cs_makuri_all",
             "h_cs_makurisashi_all", "h_cs_otherwin_all"]
RACER_STATS = {"f_rate": "h_racer_f_rate_all", "late_rate": "h_racer_late_rate_all",
               "maeduke_rate": "h_racer_maeduke_rate_all"}
# 出力に含めてはいけない列(公開しない項目)
FORBIDDEN_CARD_COLUMNS = ("age", "weight", "birthplace_number", "motor_number", "boat_number")


def card_columns() -> list[str]:
    cols = ["racer_number", "racer_name", "rank_number", "branch_number", "branch_name",
            "stadium_number", *PROGRAM_COLUMNS,
            "local365_n", "local365_win_rate", "local365_top2_rate", "local365_top3_rate"]
    for k in range(1, 7):
        cols += [f"c{k}_n", f"c{k}_win_rate"] + [f"c{k}_{s}" for s in COURSE_STATS if s != "n"]
    cols += list(RACER_STATS)
    return cols


def _started(e: pd.DataFrame) -> pd.Series:
    """出走したか(結果があり、欠場16でない)。F・L も出走に含める。"""
    pl = e["place_number"].astype("Float64")
    return (pl.notna() & (pl != 16)).fillna(False).astype(bool)


def build_cards(target: date, day_entries: pd.DataFrame, day_races: pd.DataFrame,
                hist_entries: pd.DataFrame, hist_races: pd.DataFrame,
                state=None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """D 日の出走表と、D−1 までの履歴から選手カードを作る。

    hist_* は D より前の行だけを使う(混じっていても捨てる)。state を渡すと、コース別・選手単位の
    率を状態から計算する(hist_* は結果の一覧と当地集計にだけ使うので、直近400日分あればよい)。
    """
    he = hist_entries[hist_entries["date"] < target]
    hr = hist_races[hist_races["date"] < target]

    # --- 基本と出走表(当日の最初のレースの値) ---
    de = (day_entries[day_entries["racer_number"].notna()]
          .sort_values(["race_number", "stadium_number"])
          .drop_duplicates("racer_number"))
    cards = pd.DataFrame({
        "racer_number": de["racer_number"].astype("int64").values,
        "racer_name": de["racer_name"].values,
        "rank_number": de["rank_number"].values,
        "branch_number": de["branch_number"].values,
        "stadium_number": de["stadium_number"].astype("int64").values,
    })
    cards["branch_name"] = [codes.prefecture_short_name(int(b)) if pd.notna(b) else None
                            for b in cards["branch_number"]]
    for c in PROGRAM_COLUMNS:
        cards[c] = de[c].astype("Float64").astype("float32").values

    # --- 当地(この場)の直近365日 ---
    lo = he[(he["date"] >= target - timedelta(days=LOCAL_DAYS)) & _started(he)]
    fp = lo["finish_position"].astype("Float64")
    loc = pd.DataFrame({
        "racer_number": lo["racer_number"].astype("int64"),
        "stadium_number": lo["stadium_number"].astype("int64"),
        "n": 1.0, "win": (fp == 1).fillna(False).astype(float),
        "top2": (fp <= 2).fillna(False).astype(float), "top3": (fp <= 3).fillna(False).astype(float),
    }).groupby(["racer_number", "stadium_number"]).sum().reset_index()
    cards = cards.merge(loc, on=["racer_number", "stadium_number"], how="left")
    cards["local365_n"] = cards["n"].fillna(0).astype("int32")
    for src, dst in (("win", "local365_win_rate"), ("top2", "local365_top2_rate"), ("top3", "local365_top3_rate")):
        cards[dst] = (cards[src] / cards["n"]).astype("float32")
    cards = cards.drop(columns=["n", "win", "top2", "top3"])

    # --- コース別・選手単位(style 集計。D より前だけを使う) ---
    q = pd.DataFrame({"date": [target] * len(cards), "racer_number": cards["racer_number"].astype("Float64")})
    for k in range(1, 7):
        sh = compute_style_h(q, pd.Series(float(k), index=q.index), he if state is None else None,
                             hr if state is None else None,
                             starts=state.style_starts if state is not None else None)
        cards[f"c{k}_n"] = sh["h_cs_n_all"].astype("int32").values
        cards[f"c{k}_win_rate"] = sh[WIN_PARTS].sum(axis=1).astype("float32").values
        for name, col in COURSE_STATS.items():
            if name != "n":
                cards[f"c{k}_{name}"] = sh[col].values
        if k == 1:
            for name, col in RACER_STATS.items():
                cards[name] = sh[col].values
    cards = cards[card_columns()].sort_values("racer_number").reset_index(drop=True)
    cards["rank_number"] = cards["rank_number"].astype("Int8")
    cards["branch_number"] = cards["branch_number"].astype("Int8")

    results = _results(target, day_races, de, he, hr)
    return cards, results


def _results(target: date, day_races: pd.DataFrame, de: pd.DataFrame,
             he: pd.DataFrame, hr: pd.DataFrame) -> pd.DataFrame:
    racers = set(de["racer_number"].astype("int64"))
    e = he[_started(he) & he["racer_number"].notna()].copy()
    e["racer_number"] = e["racer_number"].astype("int64")
    e = e[e["racer_number"].isin(racers)]
    e = e.merge(hr[["race_id", "technique_number"]], on="race_id", how="left")
    win = e["finish_position"].astype("Float64") == 1
    base = pd.DataFrame({
        "racer_number": e["racer_number"],
        "date": e["date"],
        "stadium_number": e["stadium_number"].astype("int8"),
        "race_number": e["race_number"].astype("int8"),
        "course_number": e["rs_course_number"].astype("Int8"),
        "place_number": e["place_number"].astype("Int8"),
        "finish_position": e["finish_position"].astype("Int8"),
        "technique_number": e["technique_number"].astype("Int8").where(win.fillna(False), pd.NA),
    })
    base = base.sort_values(["racer_number", "date", "race_number"])

    recent = base.groupby("racer_number").tail(RECENT_N).assign(kind="recent")

    # 今節: 当日のレースと同じ節(場・タイトル・連続した日)の、前日までのレース
    all_races = pd.concat([hr[["stadium_number", "date", "title"]],
                           day_races[["stadium_number", "date", "title"]]], ignore_index=True)
    smap = assign_series(all_races)
    today_series = (de[["racer_number", "stadium_number"]].assign(date=target)
                    .astype({"racer_number": "int64", "stadium_number": "int64"})
                    .merge(smap, on=["stadium_number", "date"], how="left")[["racer_number", "series_id"]])
    b2 = base.assign(stadium_number=base["stadium_number"].astype("int64")).merge(
        smap, on=["stadium_number", "date"], how="left")
    series = b2.merge(today_series, on=["racer_number", "series_id"], how="inner").drop(columns=["series_id"])
    series = series.assign(kind="series", stadium_number=series["stadium_number"].astype("int8"))

    out = pd.concat([series, recent], ignore_index=True)
    cols = ["racer_number", "kind", "date", "stadium_number", "race_number", "course_number",
            "place_number", "finish_position", "technique_number"]
    out = out[cols].sort_values(["racer_number", "kind", "date", "race_number"]).reset_index(drop=True)
    out["kind"] = out["kind"].astype("string")
    return out


def build_day(target: date, interim_dir: Path = INTERIM_DIR, out_dir: Path = CARDS_DIR,
              load_raw: LoadRaw | None = None, state=None) -> tuple[pd.DataFrame, pd.DataFrame]:
    loaded = load_day(target, load_raw or make_loader(), sources=("openapi", "turnmark"))
    if loaded is None:
        raise FileNotFoundError(f"{target} の生データがありません")
    source, raw = loaded
    t = parse.parse_day(raw, source, target)
    prev = target - timedelta(days=1)
    if state is not None:
        from src.features.history_state import load_state
        if not hasattr(state, "h_starts"):
            state = load_state(Path(state))
        if state.as_of != target:
            raise ValueError(f"状態の日付 {state.as_of} が対象日 {target} と違います")
        start = target - timedelta(days=RESULT_LOOKBACK_DAYS)
    else:
        start = None
    he = read_interim("entries", start, prev, interim_dir)
    hr = read_interim("races", start, prev, interim_dir)
    cards, results = build_cards(target, t["entries"], t["races"], he, hr, state=state)
    ymd = target.strftime("%Y%m%d")
    _write_parquet(cards, out_dir / f"{ymd}_cards.parquet")
    _write_parquet(results, out_dir / f"{ymd}_results.parquet")
    return cards, results


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="選手カードの集計(D−1 までの結果)")
    p.add_argument("--date", required=True)
    p.add_argument("--state")
    a = p.parse_args(argv)
    cards, results = build_day(date.fromisoformat(a.date), state=a.state)
    print(f"{a.date}: 選手 {len(cards)} 人 / 結果の一覧 {len(results)} 行")
    return 0


if __name__ == "__main__":
    sys.exit(main())
