"""H 特徴量(過去の結果の集計)。

リーク防止(02_features.md 3章1項): 対象日 D の値は、D より前(D−1 以前)の結果だけで作る。
実装は「キーごと・日付ごとの集計 → キーごとの累積和」を作り、対象日 D について
「D より前で最新の累積値」(= D 時点のスナップショット)を merge_asof(厳密に < D)で取る。
期間の窓 [D−W, D−1] は 累積(< D) − 累積(< D−W) で出す。行ごとの集計し直しはしない。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# 平滑化の強さ(h_racer_course_win_rate。仕様 m=10)
COURSE_SMOOTHING_M = 10.0
# 失格は平均着順の計算で7着とみなす
DISQUALIFIED_FINISH = 7
# 同じ節とみなす日付の間隔(順延で1日空くことがあるため2日まで)
SERIES_MAX_GAP_DAYS = 2


def day_ordinal(s: pd.Series) -> pd.Series:
    """date の列を、日単位の整数(1970-01-01 からの日数)にする。"""
    return (pd.to_datetime(s).values.astype("datetime64[D]").astype("int64"))


# ---------------------------------------------------------------------------
# 節(series)の判定
# ---------------------------------------------------------------------------


def assign_series(races: pd.DataFrame) -> pd.DataFrame:
    """(stadium_number, date) → series_id の対応表を作る。

    同じ場で、タイトルが同じで、日付の間隔が2日以内なら同じ節とする。
    """
    days = (
        races[["stadium_number", "date", "title"]]
        .assign(title=lambda d: d["title"].fillna("").astype(str))
        .drop_duplicates(["stadium_number", "date"])
        .sort_values(["stadium_number", "date"])
        .reset_index(drop=True)
    )
    dn = pd.Series(day_ordinal(days["date"]), index=days.index)
    new_series = (
        (days["stadium_number"] != days["stadium_number"].shift())
        | (days["title"] != days["title"].shift())
        | ((dn - dn.shift()) > SERIES_MAX_GAP_DAYS)
    )
    days["series_id"] = new_series.cumsum().astype("int64")
    return days[["stadium_number", "date", "series_id"]]


# ---------------------------------------------------------------------------
# 過去の出走の表
# ---------------------------------------------------------------------------


def make_starts(entries: pd.DataFrame, series_map: pd.DataFrame) -> pd.DataFrame:
    """過去の結果から「出走」の表を作る(欠場・未確定は除く)。

    1行=1艇の出走。着順が付いた艇と失格艇を含み、欠場(is_absent)と結果の無い行は除く。
    """
    e = entries
    started = e["place_number"].notna() & ~e["is_absent"].fillna(False).astype(bool)
    e = e.loc[started & e["racer_number"].notna()].copy()
    fp = e["finish_position"].astype("Float64")
    disq = e["is_disqualified"].fillna(False).astype(bool)
    out = pd.DataFrame({
        "date": e["date"].values,
        "dn": day_ordinal(e["date"]),
        "racer_number": e["racer_number"].astype("int64").values,
        "stadium_number": e["stadium_number"].astype("int64").values,
        "entry_number": e["entry_number"].astype("int64").values,
        "motor_number": e["motor_number"].astype("Float64").values,
        "course": e["rs_course_number"].astype("Float64").values,
        "n": 1.0,
        "win": (fp == 1).fillna(False).astype(float).values,
        "top2": (fp <= 2).fillna(False).astype(float).values,
        "top3": (fp <= 3).fillna(False).astype(float).values,
        "fin": fp.fillna(np.nan).where(~disq.values, DISQUALIFIED_FINISH).astype(float).values,
        "st": e["rs_start_timing"].astype("Float64").astype(float).values,
    })
    out["fin_n"] = out["fin"].notna().astype(float)
    out["fin"] = out["fin"].fillna(0.0)
    out["st_n"] = out["st"].notna().astype(float)
    out["st"] = out["st"].fillna(0.0)
    has_course = out["course"].notna()
    out["cc_n"] = has_course.astype(float)
    out["cc"] = (has_course & (out["course"] != out["entry_number"])).astype(float)
    out = out.merge(series_map, on=["stadium_number", "date"], how="left")
    return out


# ---------------------------------------------------------------------------
# 「D より前」の累積を取る道具
# ---------------------------------------------------------------------------


def cum_before(starts: pd.DataFrame, keys: list[str], values: list[str],
               queries: pd.DataFrame, query_dn: str = "dn") -> pd.DataFrame:
    """queries の各行(keys と日付 query_dn)について、日付 < query_dn の values の合計を返す。

    戻り値は queries と同じ index。該当が無ければ 0。
    """
    daily = (
        starts.dropna(subset=keys)
        .groupby(keys + ["dn"], sort=False)[values].sum()
        .reset_index()
        .sort_values(keys + ["dn"])
    )
    daily[values] = daily.groupby(keys, sort=False)[values].cumsum()
    for k in keys:
        daily[k] = daily[k].astype("int64")
    q = queries[keys + [query_dn]].copy()
    q["_row"] = np.arange(len(q))
    ok = q[keys].notna().all(axis=1)
    qv = q.loc[ok].copy()
    for k in keys:
        qv[k] = qv[k].astype("int64")
    qv["_q"] = qv[query_dn].astype("int64")
    qv = qv.sort_values("_q")
    right = daily.rename(columns={"dn": "_q"}).sort_values("_q")
    merged = pd.merge_asof(
        qv, right, on="_q", by=keys, direction="backward", allow_exact_matches=False,
    )
    res = pd.DataFrame(0.0, index=np.arange(len(q)), columns=values)
    res.loc[merged["_row"].values, values] = merged[values].fillna(0.0).values
    res.index = queries.index
    return res


def window_sum(starts: pd.DataFrame, keys: list[str], values: list[str],
               queries: pd.DataFrame, window_days: int | None) -> pd.DataFrame:
    """日付が [D−window_days, D−1] の合計。window_days=None なら D より前の全期間。"""
    upper = cum_before(starts, keys, values, queries, "dn")
    if window_days is None:
        return upper
    q = queries.copy()
    q["_lo"] = q["dn"] - window_days
    lower = cum_before(starts, keys, values, q, "_lo")
    return upper - lower


def last_start_before(starts: pd.DataFrame, queries: pd.DataFrame) -> pd.Series:
    """選手ごとの、D より前の最後の出走日(日単位の整数)。無ければ NaN。"""
    right = starts[["racer_number", "dn"]].drop_duplicates().sort_values("dn")
    right = right.assign(last_dn=right["dn"].astype(float))
    q = queries[["racer_number", "dn"]].copy()
    q["_row"] = np.arange(len(q))
    q = q[q["racer_number"].notna()]
    q["racer_number"] = q["racer_number"].astype("int64")
    q = q.sort_values("dn")
    merged = pd.merge_asof(q, right, on="dn", by="racer_number",
                           direction="backward", allow_exact_matches=False)
    out = pd.Series(np.nan, index=np.arange(len(queries)))
    out.loc[merged["_row"].values] = merged["last_dn"].values
    out.index = queries.index
    return out


def _rate(num: pd.Series, den: pd.Series) -> pd.Series:
    return (num / den.where(den > 0)).astype(float)


# ---------------------------------------------------------------------------
# H 特徴量
# ---------------------------------------------------------------------------


def compute_h_features(targets: pd.DataFrame, hist_entries: pd.DataFrame,
                       series_map: pd.DataFrame) -> pd.DataFrame:
    """targets(1行=1艇。date, racer_number, stadium_number, entry_number, motor_number, series_id)
    に対する H 特徴量を返す(targets と同じ index)。

    hist_entries は interim の entries。D 以降の行が含まれていても、D より前しか使わない。
    """
    starts = make_starts(hist_entries, series_map)
    q = pd.DataFrame({
        "dn": day_ordinal(targets["date"]),
        "racer_number": targets["racer_number"].astype("Float64").values,
        "stadium_number": targets["stadium_number"].astype("int64").values,
        # 前日モデルでは枠番 = 進入コースと仮定する
        "course": targets["entry_number"].astype("Float64").values,
        "motor_number": targets["motor_number"].astype("Float64").values,
        "series_id": targets["series_id"].astype("Float64").values,
    }, index=targets.index)
    out = pd.DataFrame(index=targets.index)

    # 選手の直近90日
    w = window_sum(starts, ["racer_number"],
                   ["n", "win", "top2", "top3", "fin", "fin_n", "st", "st_n"], q, 90)
    out["h_racer_n_90d"] = w["n"]
    out["h_racer_win_rate_90d"] = _rate(w["win"], w["n"])
    out["h_racer_top2_90d"] = _rate(w["top2"], w["n"])
    out["h_racer_top3_90d"] = _rate(w["top3"], w["n"])
    out["h_racer_avg_finish_90d"] = _rate(w["fin"], w["fin_n"])
    out["h_racer_real_st_90d"] = _rate(w["st"], w["st_n"])

    # 同じコースでの1着率(全期間、ベイズ平滑化)。事前分布は D 時点の全体のコース別1着率
    rc = window_sum(starts, ["racer_number", "course"], ["n", "win"], q, None)
    prior = window_sum(starts, ["course"], ["n", "win"], q, None)
    prior_rate = _rate(prior["win"], prior["n"])
    m = COURSE_SMOOTHING_M
    out["h_racer_course_win_rate"] = (rc["win"] + m * prior_rate) / (rc["n"] + m)

    # 前付けの傾向(全期間)
    cc = window_sum(starts, ["racer_number"], ["cc", "cc_n"], q, None)
    out["h_racer_course_change_rate"] = _rate(cc["cc"], cc["cc_n"])

    # 場・コース別の1着率(365日)
    sc = window_sum(starts, ["stadium_number", "course"], ["n", "win"], q, 365)
    out["h_stadium_course_win_rate_365d"] = _rate(sc["win"], sc["n"])

    # 同じ節の前日まで: モーターの2連対率、選手の平均着順
    ms = window_sum(starts, ["series_id", "motor_number"], ["n", "top2"], q, None)
    out["h_motor_series_top2"] = _rate(ms["top2"], ms["n"])
    rs = window_sum(starts, ["series_id", "racer_number"], ["fin", "fin_n"], q, None)
    out["h_racer_series_avg_finish"] = _rate(rs["fin"], rs["fin_n"])

    # 前回の出走からの日数
    last = last_start_before(starts, q)
    out["h_days_since_last_race"] = q["dn"] - last

    return out.astype("float32")
