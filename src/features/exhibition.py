"""展示(スタート展示・展示タイム)のレース内の相対値(直前版 v3。2026-11-06 追加)。

背景: 10年分で「予定4〜6コースの3艇のうち、展示ST が一番早い艇は連に絡みやすい」ことが確認された。
予定コースは展示の進入(pv_course_number。無ければ枠番)。外側 = 予定4〜6コース、内側 = 1〜3コース。

展示ST が F(負の値)の扱い:
  - 順位では「最速」とみなす(F が複数いれば同順位)。
  - 差の計算では 0.00 とみなす(負の値のまま引くと差が大きく出すぎるため)。
  - F だったことは pv_st_flying(1/0)で別に持つ。
展示ST・展示タイムが無い艇は、順位・差・フラグとも NaN。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

EX_FEATURES: list[str] = [
    "r_pv_st_rank",                  # 展示ST のレース内順位(1=最速。F は最速扱い)
    "pv_st_flying",                  # 展示ST が F(負の値)だったか
    "r_pv_st_rank_group",            # 展示ST のグループ内順位(外側 4〜6 / 内側 1〜3 の中で)
    "r_pv_st_diff_best",             # 展示ST − レース内の最速(F は 0.00 として)
    "r_pv_st_diff_inner",            # 展示ST − すぐ内側(予定コース −1)の艇の展示ST。1コースは NaN
    "r_pv_exhibition_rank_group",    # 展示タイムのグループ内順位
    "pv_outer_both_top",             # 外側グループで展示ST・展示タイムがともに1位か(内側の艇は 0)
]


def compute_ex_features(df: pd.DataFrame, planned: pd.Series) -> pd.DataFrame:
    """df: race_id, pv_start_timing, pv_exhibition_time を持つ艇の表。planned: 予定コース。"""
    rid = df["race_id"]
    c = planned.astype("Float64").astype(float)
    st = df["pv_start_timing"].astype("Float64").astype(float)
    ex = df["pv_exhibition_time"].astype("Float64").astype(float)
    flying = st < 0
    key = st.where(~flying, -1.0)        # 順位用: F は最速
    st0 = st.clip(lower=0)               # 差用: F は 0.00
    group = np.where(c >= 4, "outer", np.where(c >= 1, "inner", None))
    gkey = [rid, pd.Series(group, index=df.index)]

    out = pd.DataFrame(index=df.index)
    out["r_pv_st_rank"] = key.groupby(rid).rank(method="min")
    out["pv_st_flying"] = flying.astype(float).where(st.notna())
    out["r_pv_st_rank_group"] = key.groupby(gkey).rank(method="min")
    out["r_pv_st_diff_best"] = st0 - st0.groupby(rid).transform("min")
    # すぐ内側の艇(予定コース −1)の展示ST。同じ予定コースが複数いれば枠番の小さい艇
    look = pd.DataFrame({"race_id": rid, "c": c, "st0": st0, "en": df["entry_number"]})
    look = look.dropna(subset=["c"]).sort_values(["race_id", "c", "en"]).drop_duplicates(["race_id", "c"])
    inner = pd.DataFrame({"race_id": rid, "c": c - 1}).merge(
        look[["race_id", "c", "st0"]], on=["race_id", "c"], how="left")["st0"].values
    out["r_pv_st_diff_inner"] = st0 - pd.Series(inner, index=df.index)
    out["r_pv_exhibition_rank_group"] = ex.groupby(gkey).rank(method="min")
    is_outer = pd.Series(group == "outer", index=df.index)
    both = (out["r_pv_st_rank_group"] == 1) & (out["r_pv_exhibition_rank_group"] == 1) & is_outer
    # 自分の展示ST か展示タイムが無い艇は NaN(内側の艇も同じ。直前情報が無いのに 0 にしない)
    out["pv_outer_both_top"] = both.astype(float).where(st.notna() & ex.notna())
    return out.astype("float32")
