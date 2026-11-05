"""件数の扱いなどの共通ルール。正本は PM の src/common/analytics_rules.py(P-6)。

ここでは DataFrame の列にまとめてかけるための薄い包みだけを置く(値・区分は正本のものを使う)。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.common import analytics_rules as ar

MIN_COUNT_SHOW = ar.MIN_COUNT_SHOW


def count_level(n) -> pd.Series:
    """件数 → 表示の区分(normal / few / insufficient)。"""
    n = pd.Series(n)
    return n.map(lambda v: ar.sample_level(None if pd.isna(v) else int(v)))


def wilson(k, n) -> tuple[pd.Series, pd.Series]:
    """率の95%区間(Wilson)。n=0 は NaN。"""
    k, n = pd.Series(k, dtype="float64"), pd.Series(n, dtype="float64")
    iv = [ar.wilson_interval(float(a), int(b)) if b > 0 else None for a, b in zip(k, n.reindex(k.index))]
    lo = pd.Series([v[0] if v else np.nan for v in iv], index=k.index)
    hi = pd.Series([v[1] if v else np.nan for v in iv], index=k.index)
    return lo, hi


def mask_rates(df: pd.DataFrame, n_col: str, rate_cols: list[str]) -> pd.DataFrame:
    """件数が下限未満の行は率を消す(件数だけ残す。rate_or_none と同じ基準)。"""
    df = df.copy()
    low = df[n_col] < ar.MIN_COUNT_SHOW
    for c in rate_cols:
        df.loc[low, c] = np.nan
    return df
