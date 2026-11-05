"""レース運び(決まり手・ST・前付け・着の取り方)の特徴量。variant が *_v2 のときだけ使う。

H 区分: 選手 × 実際の進入コース(rs_course_number)ごとの過去の集計を、今回の予定コース
(前日版=枠番、直前版=pv_course_number。無ければ枠番)に対応させて各艇に付ける。
D 日の値は D−1 日以前の結果だけで作る(history.window_sum の仕組みをそのまま使う)。

件数が少ないので、率はすべて「全体平均への縮小」をかける(経験ベイズ的):
    (k + α・p) / (n + α)     p = D 時点の全体の率(コース別の値は同じコースの全体平均)
件数 n 自体も特徴量にする。期間は累計(_all)と直近180日(_180d)の両方を出す。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.features.history import day_ordinal, window_sum

# 縮小の強さ α
STYLE_ALPHA = 10.0
# 期間: (列名の接尾辞, 日数。None は累計)
STYLE_WINDOWS: tuple[tuple[str, int | None], ...] = (("all", None), ("180d", 180))

# 選手×コースの「出走あたりの率」(分子の列名 → 特徴量名の一部)
COURSE_RATE_VALUES: dict[str, str] = {
    "nige": "nige",            # 1着・逃げ
    "sashi": "sashi",          # 1着・差し
    "makuri": "makuri",        # 1着・まくり
    "msashi": "makurisashi",   # 1着・まくり差し
    "owin": "otherwin",        # 1着・その他(抜き・恵まれ・不明)
    "second": "second",        # 2着
    "third": "third",          # 3着
    "low": "low",              # 5・6着、失格、F・L
}
# 選手単位(全コース)の率
RACER_RATE_VALUES: dict[str, tuple[str, str]] = {
    "f": ("f_rate", "n"),           # フライング(place 14)
    "l": ("late_rate", "n"),        # 出遅れ(place 15)
    "mae": ("maeduke_rate", "mae_n"),  # 枠番より内のコースに入った割合
}


def style_h_feature_names() -> list[str]:
    names: list[str] = []
    for w, _ in STYLE_WINDOWS:
        names.append(f"h_cs_n_{w}")
        names += [f"h_cs_{v}_{w}" for v in COURSE_RATE_VALUES.values()]
        names += [f"h_cs_st_mean_{w}", f"h_cs_st_std_{w}"]
        names += [f"h_racer_{v[0]}_{w}" for v in RACER_RATE_VALUES.values()]
    return names


STYLE_R_FEATURES: list[str] = [
    "r_sty_in_nige",              # 予定1コースの艇の逃げ率
    "r_sty_nige_minus_sashi2",    # 1コースの逃げ率 − 2コースの差し率
    "r_sty_nige_minus_makuri34",  # 1コースの逃げ率 − 3・4コースの(まくり+まくり差し)率の最大
    "r_sty_st_diff_inner",        # 自分の平均ST − 内側(予定コースが小さい)艇の平均STの平均。1コースは0
]


def make_style_starts(entries: pd.DataFrame, races: pd.DataFrame) -> pd.DataFrame:
    """過去の出走の表(1行=1艇の出走)。F・L(14・15)は含め、欠場(16)と結果なしは除く。"""
    e = entries.merge(races[["race_id", "technique_number"]], on="race_id", how="left")
    pl = e["place_number"].astype("Float64")
    ok = pl.notna() & (pl != 16) & (pl != 99) & e["racer_number"].notna()
    e, pl = e.loc[ok.fillna(False).astype(bool)], pl[ok.fillna(False).astype(bool)]
    fp = e["finish_position"].astype("Float64")
    tech = e["technique_number"].astype("Float64")
    win = (fp == 1).fillna(False).astype(bool)
    fl = pl.isin([14, 15]).fillna(False).astype(bool)
    disq = e["is_disqualified"].fillna(False).astype(bool)
    course = e["rs_course_number"].astype("Float64")
    st = e["rs_start_timing"].astype("Float64").astype(float)
    out = pd.DataFrame({
        "dn": day_ordinal(e["date"]),
        "racer_number": e["racer_number"].astype("int64").values,
        "course": course.values,
        "_all": 0,
        "n": 1.0,
        "nige": (win & (tech == 1).fillna(False)).astype(float).values,
        "sashi": (win & (tech == 2).fillna(False)).astype(float).values,
        "makuri": (win & (tech == 3).fillna(False)).astype(float).values,
        "msashi": (win & (tech == 4).fillna(False)).astype(float).values,
        "owin": (win & ~tech.isin([1, 2, 3, 4]).fillna(False)).astype(float).values,
        "second": (fp == 2).fillna(False).astype(float).values,
        "third": (fp == 3).fillna(False).astype(float).values,
        "low": ((fp >= 5).fillna(False) | disq | fl).astype(float).values,
        "f": (pl == 14).fillna(False).astype(float).values,
        "l": (pl == 15).fillna(False).astype(float).values,
    })
    st_ok = ~np.isnan(st.values)
    out["st_n"] = st_ok.astype(float)
    out["st"] = np.where(st_ok, st.values, 0.0)
    out["st2"] = np.where(st_ok, st.values ** 2, 0.0)
    has_c = course.notna().fillna(False).astype(bool).values
    out["mae_n"] = has_c.astype(float)
    out["mae"] = (has_c & (course.fillna(99).values < e["entry_number"].astype(int).values)).astype(float)
    # date 列は window_sum では使わないが、make_starts と形をそろえる
    return out


def _shrink(k: pd.Series, n: pd.Series, p: pd.Series, alpha: float = STYLE_ALPHA) -> pd.Series:
    return ((k + alpha * p) / (n + alpha)).astype(float)


def _rate(num: pd.Series, den: pd.Series) -> pd.Series:
    return (num / den.where(den > 0)).astype(float)


def compute_style_h(targets: pd.DataFrame, planned_course: pd.Series,
                    hist_entries: pd.DataFrame, hist_races: pd.DataFrame) -> pd.DataFrame:
    """targets(date, racer_number)と予定コースから、レース運びの H 特徴量を作る。"""
    starts = make_style_starts(hist_entries, hist_races)
    q = pd.DataFrame({
        "dn": day_ordinal(targets["date"]),
        "racer_number": targets["racer_number"].astype("Float64").values,
        "course": planned_course.astype("Float64").values,
        "_all": 0,
    }, index=targets.index)
    cvals = list(COURSE_RATE_VALUES) + ["n", "st", "st2", "st_n"]
    rvals = ["n", "f", "l", "mae", "mae_n"]
    out = pd.DataFrame(index=targets.index)
    for w, days in STYLE_WINDOWS:
        rc = window_sum(starts, ["racer_number", "course"], cvals, q, days)
        pc = window_sum(starts, ["course"], cvals, q, days)
        out[f"h_cs_n_{w}"] = rc["n"]
        for src, name in COURSE_RATE_VALUES.items():
            out[f"h_cs_{name}_{w}"] = _shrink(rc[src], rc["n"], _rate(pc[src], pc["n"]))
        p_st, p_st2 = _rate(pc["st"], pc["st_n"]), _rate(pc["st2"], pc["st_n"])
        mean = _shrink(rc["st"], rc["st_n"], p_st)
        e2 = _shrink(rc["st2"], rc["st_n"], p_st2)
        out[f"h_cs_st_mean_{w}"] = mean
        out[f"h_cs_st_std_{w}"] = np.sqrt((e2 - mean ** 2).clip(lower=0))
        rr = window_sum(starts, ["racer_number"], rvals, q, days)
        pr = window_sum(starts, ["_all"], rvals, q, days)
        for src, (name, den) in RACER_RATE_VALUES.items():
            out[f"h_racer_{name}_{w}"] = _shrink(rr[src], rr[den], _rate(pr[src], pr[den]))
    return out.astype("float32")


def compute_style_r(df: pd.DataFrame, planned_course: pd.Series, h: pd.DataFrame) -> pd.DataFrame:
    """レース内の組み合わせ(R)。累計(_all)の値を使う。"""
    rid = df["race_id"]
    c = planned_course.astype("Float64").astype(float)
    nige = h["h_cs_nige_all"].astype(float)
    sashi = h["h_cs_sashi_all"].astype(float)
    attack = (h["h_cs_makuri_all"] + h["h_cs_makurisashi_all"]).astype(float)
    st = h["h_cs_st_mean_all"].astype(float)

    def per_race(values: pd.Series, mask: pd.Series) -> pd.Series:
        return values.where(mask).groupby(rid).transform("max")

    in_nige = per_race(nige, c == 1)
    out = pd.DataFrame(index=df.index)
    out["r_sty_in_nige"] = in_nige
    out["r_sty_nige_minus_sashi2"] = in_nige - per_race(sashi, c == 2)
    out["r_sty_nige_minus_makuri34"] = in_nige - per_race(attack, c.isin([3, 4]))
    # 内側の艇との平均ST差(予定コースの小さい順に並べ、累積平均を1つずらす)
    order = np.lexsort((c.fillna(99).to_numpy(), rid.to_numpy()))
    s, r_sorted = st.iloc[order], rid.iloc[order].values
    csum = s.fillna(0).groupby(r_sorted).cumsum() - s.fillna(0)
    ccnt = s.notna().astype(float).groupby(r_sorted).cumsum() - s.notna().astype(float)
    inner_mean = (csum / ccnt.where(ccnt > 0)).reindex(df.index)
    out["r_sty_st_diff_inner"] = (st - inner_mean).where(ccnt.reindex(df.index) > 0, 0.0)
    return out.astype("float32")
