"""履歴の集計の状態(スナップショット)。毎日のジョブを軽くするため(F-8。spec/10 2.4節)。

状態 state_YYYYMMDD は「YYYYMMDD の特徴量を作るための、YYYYMMDD−1 までの履歴」。中身:

- h_starts / style_starts: H・style 特徴量の材料の「出走」の表(history.make_starts /
  style.make_style_starts と同じ列)。
  - 直近 KEEP_DAYS(=365)日分は1出走1行のまま持つ(期間の窓 90・180・365日 に使う)。
  - それより古い行は、キーごとに1行へ合計して畳む。畳んだ行の日付(dn)は、その中の最後の出走日。
    窓の境目(D−W, W≦365)より必ず前にあるので、累計にも窓にも正しく効く。
    最後の出走日(h_days_since_last_race)も変わらない。
    節(series_id)とモーター番号は、1年以上前の節には当日の艇が当たらないので null にして畳む。
- races: 節の判定に使う直近 RACE_KEEP_DAYS 日の (場, 日付, タイトル)。

当日の特徴量は「状態 + 当日の出走表」から、学習用と同じ関数(make_feature_frame)で作る。
全履歴から作った値と完全に一致することをテストで確かめている。

使い方:
    # interim の全履歴から作る(初回・作り直し)
    python -m src.features.history_state build --as-of 2026-11-05
    # 前日分が確定したら1日進める(state_20261105 + 11-05 の結果 → state_20261106)
    python -m src.features.history_state advance --state data/history/state_20261105.parquet
    # 状態から当日の特徴量
    python -m src.features.build_features --variant pre --date 2026-11-06 --state data/history/state_20261106.parquet
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from src.features.build_interim import INTERIM_DIR, read_interim
from src.features.history import assign_series, day_ordinal, make_starts
from src.features.style import make_style_starts

ROOT = Path(__file__).resolve().parents[2]
HISTORY_DIR = ROOT / "data" / "history"

# 1出走1行で持つ日数(使っている窓の最大 = 365日)
KEEP_DAYS = 365
# 節の判定のために持つレースの日数
RACE_KEEP_DAYS = 400

# 古い行を畳むときのキー(残りのキー列は null にする)
H_COLLAPSE_KEYS = ["racer_number", "course", "stadium_number"]
STYLE_COLLAPSE_KEYS = ["racer_number", "course", "_all"]
H_NULLED_KEYS = ["motor_number", "series_id", "entry_number"]


@dataclass
class HistoryState:
    as_of: date               # この日の特徴量を作るための状態(結果は as_of−1 まで)
    h_starts: pd.DataFrame
    style_starts: pd.DataFrame
    races: pd.DataFrame       # stadium_number, date, title


def _dn(d: date) -> int:
    return int(np.datetime64(d, "D").astype("int64"))


def _collapse(starts: pd.DataFrame, as_of: date, keys: list[str], nulled: list[str]) -> pd.DataFrame:
    """as_of − KEEP_DAYS より前の行を、キーごとに1行へ畳む。"""
    if starts.empty:
        return starts.reset_index(drop=True)
    limit = _dn(as_of) - KEEP_DAYS
    old = starts[starts["dn"] < limit]
    recent = starts[starts["dn"] >= limit]
    if old.empty:
        return recent.reset_index(drop=True)
    values = [c for c in starts.columns if c not in keys + nulled + ["dn", "date"]]
    agg = {c: "sum" for c in values}
    agg["dn"] = "max"
    folded = old.groupby(keys, dropna=False, sort=False).agg(agg).reset_index()
    for c in nulled:
        if c in starts.columns:
            folded[c] = np.nan
    folded = folded[[c for c in starts.columns if c in folded.columns]]
    out = pd.concat([folded, recent], ignore_index=True)
    return out


def _new_starts(entries: pd.DataFrame, races: pd.DataFrame, series_map: pd.DataFrame
                ) -> tuple[pd.DataFrame, pd.DataFrame]:
    h = make_starts(entries, series_map).drop(columns=["date"])
    st = make_style_starts(entries, races)
    return h, st


def _trim_races(races: pd.DataFrame, as_of: date) -> pd.DataFrame:
    r = races[["stadium_number", "date", "title"]].drop_duplicates(["stadium_number", "date"])
    r = r[(r["date"] < as_of) & (r["date"] >= as_of - timedelta(days=RACE_KEEP_DAYS))]
    return r.sort_values(["stadium_number", "date"]).reset_index(drop=True)


def build_state(entries: pd.DataFrame, races: pd.DataFrame, as_of: date) -> HistoryState:
    """interim(全履歴)から、as_of の特徴量を作るための状態を作る(結果は as_of−1 まで)。"""
    e = entries[entries["date"] < as_of]
    r = races[races["date"] < as_of]
    series_map = assign_series(r)
    h, st = _new_starts(e, r, series_map)
    return HistoryState(
        as_of=as_of,
        h_starts=_collapse(h, as_of, H_COLLAPSE_KEYS, H_NULLED_KEYS),
        style_starts=_collapse(st, as_of, STYLE_COLLAPSE_KEYS, []),
        races=_trim_races(r, as_of),
    )


def advance_state(state: HistoryState, day_entries: pd.DataFrame, day_races: pd.DataFrame) -> HistoryState:
    """state(as_of=D)に D 日の結果を足して、D+1 の状態にする。"""
    d = state.as_of
    e = day_entries[day_entries["date"] == d]
    r = day_races[day_races["date"] == d]
    all_races = pd.concat([state.races, r[["stadium_number", "date", "title"]]], ignore_index=True)
    series_map = assign_series(all_races)
    h, st = _new_starts(e, r, series_map)
    nxt = d + timedelta(days=1)
    return HistoryState(
        as_of=nxt,
        h_starts=_collapse(pd.concat([state.h_starts, h], ignore_index=True), nxt,
                           H_COLLAPSE_KEYS, H_NULLED_KEYS),
        style_starts=_collapse(pd.concat([state.style_starts, st], ignore_index=True), nxt,
                               STYLE_COLLAPSE_KEYS, []),
        races=_trim_races(all_races, nxt),
    )


# ---------------------------------------------------------------------------
# 保存と読み込み(1ファイル。_table 列で3つの表を区別する)
# ---------------------------------------------------------------------------


def state_path(as_of: date, history_dir: Path = HISTORY_DIR) -> Path:
    return history_dir / f"state_{as_of.strftime('%Y%m%d')}.parquet"


def save_state(state: HistoryState, path: Path | None = None) -> Path:
    path = path or state_path(state.as_of)
    path.parent.mkdir(parents=True, exist_ok=True)
    parts = []
    for name, df in (("h", state.h_starts), ("style", state.style_starts)):
        parts.append(df.add_prefix(f"{name}__").assign(_table=name))
    races = state.races.assign(dn=day_ordinal(state.races["date"])).drop(columns=["date"])
    parts.append(races.add_prefix("races__").assign(_table="races"))
    out = pd.concat(parts, ignore_index=True)
    out["_as_of"] = state.as_of.isoformat()
    for c in out.columns:
        if c.endswith("title"):
            out[c] = out[c].astype("string")
        elif c not in ("_table", "_as_of"):
            out[c] = out[c].astype("float64")
    tmp = path.with_name(path.name + ".tmp")
    out.to_parquet(tmp, index=False)
    tmp.replace(path)
    return path


def load_state(path: Path) -> HistoryState:
    df = pd.read_parquet(path)
    as_of = date.fromisoformat(str(df["_as_of"].iloc[0]))

    def part(name: str) -> pd.DataFrame:
        sub = df[df["_table"] == name]
        cols = [c for c in df.columns if c.startswith(f"{name}__")]
        sub = sub[cols].rename(columns=lambda c: c.split("__", 1)[1]).reset_index(drop=True)
        return sub

    h, st, races = part("h"), part("style"), part("races")
    for t in (h, st):
        t["dn"] = t["dn"].astype("int64")
        t["racer_number"] = t["racer_number"].astype("int64")
    st["_all"] = st["_all"].astype("int64")
    h["stadium_number"] = h["stadium_number"].astype("int64")
    races = pd.DataFrame({
        "stadium_number": races["stadium_number"].astype("int64"),
        "date": pd.to_datetime(races["dn"].astype("int64"), unit="D").dt.date,
        "title": races["title"],
    })
    return HistoryState(as_of=as_of, h_starts=h, style_starts=st, races=races)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="履歴の集計の状態(スナップショット)を作る・進める")
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="interim の全履歴から作る")
    b.add_argument("--as-of", required=True, help="この日の特徴量用(結果は前日まで)。YYYY-MM-DD")
    a = sub.add_parser("advance", help="1日進める(状態の as_of の日の結果を interim から足す)")
    a.add_argument("--state", required=True)
    a.add_argument("--days", type=int, default=1)
    for x in (b, a):
        x.add_argument("--out-dir", default=str(HISTORY_DIR))
    args = p.parse_args(argv)
    out_dir = Path(args.out_dir)
    if args.cmd == "build":
        as_of = date.fromisoformat(args.as_of)
        prev = as_of - timedelta(days=1)
        st = build_state(read_interim("entries", None, prev), read_interim("races", None, prev), as_of)
    else:
        st = load_state(Path(args.state))
        for _ in range(args.days):
            d = st.as_of
            st = advance_state(st, read_interim("entries", d, d), read_interim("races", d, d))
    path = save_state(st, state_path(st.as_of, out_dir))
    print(f"{path}: h={len(st.h_starts)} style={len(st.style_starts)} races={len(st.races)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
