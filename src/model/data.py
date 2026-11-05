"""特徴量テーブルの読み込み・絞り込み・レース単位の行列化・softmax。

特徴量の列リストは src/features/feature_sets.py(特徴量担当Fの持ち物)を import して使う。
まだ無い間だけ、spec/02 2.2章をもとにした仮のリスト(_FALLBACK_*)を使う。
仮のリストを使ったかどうかは feature_lists() の戻り値の "source" に残す。
"""

from __future__ import annotations

import hashlib
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

from src.model.config import FEATURES_DIR, N_BOATS, VARIANTS

# 特徴量に含めてはいけない文字列(spec/02 3章2項)
LEAK_SUBSTRINGS = ("rs_", "place", "odds", "payout", "technique")

# ---------------------------------------------------------------------------
# 仮の特徴量リスト(feature_sets.py が出来たら使われなくなる)
# ---------------------------------------------------------------------------
_FALLBACK_PRE = [
    # P: 出走表
    "entry_number", "stadium_number", "race_number", "grade_number", "day_number", "race_kind",
    "rank_number", "age", "weight", "flying_count", "late_count", "average_start_timing",
    "national_win_rate", "national_top_2_percent", "national_top_3_percent",
    "local_win_rate", "local_top_2_percent", "local_top_3_percent", "local_win_rate_missing",
    "motor_top_2_percent", "motor_top_3_percent", "boat_top_2_percent", "boat_top_3_percent",
    "is_home_branch",
    # R: レース内の相対値
    "r_win_rate_rank", "r_win_rate_diff_max", "r_win_rate_diff_mean", "r_st_rank",
    "r_motor2_rank", "r_inner_strength", "r_a1_count",
    # H: 過去の集計
    "h_racer_n_90d", "h_racer_win_rate_90d", "h_racer_top2_90d", "h_racer_top3_90d",
    "h_racer_avg_finish_90d", "h_racer_course_win_rate", "h_racer_real_st_90d",
    "h_racer_course_change_rate", "h_stadium_course_win_rate_365d", "h_motor_series_top2",
    "h_racer_series_avg_finish", "h_days_since_last_race",
]
_FALLBACK_PREVIEW_ONLY = [
    "pv_exhibition_time", "r_pv_exhibition_rank", "r_pv_exhibition_diff_best",
    "pv_start_timing", "pv_course_number", "pv_course_changed",
    "pv_tilt_adjustment", "pv_weight_adjustment", "pv_propeller_new", "pv_parts_count",
    "pv_wind_speed", "pv_wave_height", "pv_air_temperature", "pv_water_temperature",
    "pv_wind_class", "pv_weather_number",
]
_FALLBACK_CATEGORICAL = ["stadium_number", "grade_number", "race_kind", "pv_wind_class", "pv_weather_number"]

# キー・目的変数・メタ列(spec/02 2.1章)
KEY_COLUMNS = [
    "race_id", "date", "stadium_number", "race_number", "entry_number", "racer_number",
    "finish_position", "is_win", "is_top2", "is_top3", "is_valid_result", "is_absent",
]


def feature_lists(variant: str) -> dict:
    """variant の特徴量列とカテゴリ列を返す。

    Returns:
        {"features": [...], "categorical": [...], "source": "feature_sets" | "fallback"}
    """
    if variant not in VARIANTS:
        raise ValueError(f"variant が不正です: {variant}")
    try:
        from src.features import feature_sets  # type: ignore[import-not-found]

        features = feature_sets.features_for(variant)
        categorical = feature_sets.categorical_features(variant)
        source = "feature_sets"
    except ImportError:
        warnings.warn(
            "src.features.feature_sets が見つからないため、spec/02 をもとにした仮の特徴量リストを使います",
            stacklevel=2,
        )
        features = list(_FALLBACK_PRE) + (list(_FALLBACK_PREVIEW_ONLY) if variant.startswith("preview") else [])
        categorical = [c for c in _FALLBACK_CATEGORICAL if c in features]
        source = "fallback"
    check_no_leak(features)
    return {"features": features, "categorical": categorical, "source": source}


def check_no_leak(features: list[str]) -> None:
    """特徴量の列名にリークのおそれがある文字列が含まれていないことを確認する。"""
    bad = [c for c in features if any(s in c for s in LEAK_SUBSTRINGS)]
    if bad:
        raise ValueError(f"特徴量にリークのおそれがある列があります: {bad}")


# ---------------------------------------------------------------------------
# 読み込み・絞り込み
# ---------------------------------------------------------------------------


def features_path(variant: str) -> Path:
    return FEATURES_DIR / f"features_{variant}.parquet"


def load_features(path: str | Path) -> pd.DataFrame:
    """特徴量テーブルを読み、date を datetime64 にそろえる。"""
    df = pd.read_parquet(path)
    return normalize_frame(df)


def normalize_frame(df: pd.DataFrame) -> pd.DataFrame:
    """型をそろえる(date は datetime、フラグは bool。キーが無い・null は欠損として扱う)。"""
    df = df.copy()
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"])
    for col in ("is_valid_result", "is_absent"):
        if col in df.columns:
            # null は False(is_valid_result が null のレースは使わない。is_absent が null は欠場ではない)
            df[col] = df[col].astype("boolean").fillna(False).astype(bool)
    for col in ("is_win", "is_top2", "is_top3"):
        if col in df.columns:
            # boolean(NA あり)→ float(NA は NaN)。学習・評価では NaN の行は使わない
            df[col] = df[col].astype("Float64").astype(float)
    return df


def filter_period(df: pd.DataFrame, date_from: str, date_to: str) -> pd.DataFrame:
    """date が [date_from, date_to] の行を返す(両端を含む)。"""
    d = df["date"]
    return df[(d >= pd.Timestamp(date_from)) & (d <= pd.Timestamp(date_to))]


def clean_race_mask(df: pd.DataFrame) -> pd.Series:
    """評価に使える行か(spec/03 4章)。

    条件: is_valid_result、レース内に欠場・返還が無い、6艇そろっている、1着がちょうど1艇。
    """
    g = df.groupby("race_id", sort=False)
    n_rows = g["entry_number"].transform("size")
    n_absent = g["is_absent"].transform("sum")
    n_win = (df["finish_position"] == 1).groupby(df["race_id"], sort=False).transform("sum")
    valid = g["is_valid_result"].transform("all")
    return valid & (n_absent == 0) & (n_rows == N_BOATS) & (n_win == 1)


def train_row_mask(df: pd.DataFrame) -> pd.Series:
    """学習に使える行か(spec/02 3章6項、1.5章)。

    欠場・返還の行は外す。中止レースと同着1着のレースも外す。
    """
    n_win = (df["finish_position"] == 1).groupby(df["race_id"], sort=False).transform("sum")
    return df["is_valid_result"] & ~df["is_absent"] & (n_win == 1)


def exclusion_counts(df: pd.DataFrame) -> dict[str, int]:
    """評価から外したレースの数を理由ごとに返す(理由は重複して数える)。"""
    g = df.groupby("race_id", sort=False)
    races = g.agg(
        valid=("is_valid_result", "all"),
        n_absent=("is_absent", "sum"),
        n_rows=("entry_number", "size"),
    )
    races["n_win"] = (df["finish_position"] == 1).groupby(df["race_id"], sort=False).sum()
    return {
        "races_total": int(len(races)),
        "invalid_result": int((~races["valid"]).sum()),
        "has_absent": int((races["n_absent"] > 0).sum()),
        "not_6_boats": int((races["n_rows"] != N_BOATS).sum()),
        "dead_heat_or_no_winner": int((races["valid"] & (races["n_win"] != 1)).sum()),
        "races_used": int(
            (races["valid"] & (races["n_absent"] == 0) & (races["n_rows"] == N_BOATS) & (races["n_win"] == 1)).sum()
        ),
    }


def data_hash(df: pd.DataFrame, columns: list[str] | None = None) -> str:
    """学習に使った表のハッシュ(meta.json 用)。行と列の並びに依存する。"""
    sub = df if columns is None else df[columns]
    h = pd.util.hash_pandas_object(sub, index=False).to_numpy()
    return hashlib.sha256(h.tobytes() + ",".join(map(str, sub.columns)).encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# レース単位の行列化と softmax
# ---------------------------------------------------------------------------


def race_matrix(df: pd.DataFrame, value_col: str, fill: float = np.nan) -> tuple[np.ndarray, np.ndarray]:
    """1行=1艇の表を (レース数, 6) の行列にする。列は枠番1〜6の順。

    Returns:
        (race_ids, matrix)。race_ids は df に出てくる順。足りない枠番は fill。
    """
    race_ids, codes = pd.factorize(df["race_id"], sort=False)
    entry = df["entry_number"].to_numpy().astype(int)
    if np.any((entry < 1) | (entry > N_BOATS)):
        raise ValueError("entry_number が1〜6の範囲外です")
    mat = np.full((len(codes), N_BOATS), fill, dtype=np.float64)
    mat[race_ids, entry - 1] = df[value_col].to_numpy(dtype=np.float64, na_value=np.nan)
    return np.asarray(codes), mat


def race_softmax(race_ids: np.ndarray | pd.Series, scores: np.ndarray, temperature: float = 1.0) -> np.ndarray:
    """レース内で softmax(scores / T) をとる(spec/03 2.1章)。戻り値は入力と同じ並び。"""
    if not temperature > 0:
        raise ValueError("temperature は正の数が必要です")
    codes, _ = pd.factorize(np.asarray(race_ids), sort=False)
    z = np.asarray(scores, dtype=np.float64) / temperature
    zmax = np.full(codes.max() + 1 if len(codes) else 0, -np.inf)
    np.maximum.at(zmax, codes, z)
    e = np.exp(z - zmax[codes])
    denom = np.bincount(codes, weights=e)
    return e / denom[codes]


def win_logloss_rows(race_ids: np.ndarray, p_win: np.ndarray, is_win: np.ndarray) -> float:
    """1着艇の -log p の平均(レース単位)。is_win==1 の行が各レースちょうど1つある前提。"""
    p = np.clip(np.asarray(p_win)[np.asarray(is_win) == 1], 1e-15, 1.0)
    return float(-np.mean(np.log(p)))


def fit_temperature(
    race_ids: np.ndarray,
    scores: np.ndarray,
    is_win: np.ndarray,
    bounds: tuple[float, float] = (0.2, 5.0),
    tol: float = 1e-4,
) -> tuple[float, float]:
    """多クラス logloss が最小になる温度 T を黄金分割法で探す(spec/03 2.1章)。

    Returns:
        (T, そのときの logloss)
    """
    race_ids = np.asarray(race_ids)
    scores = np.asarray(scores, dtype=np.float64)
    is_win = np.asarray(is_win)

    def loss(t: float) -> float:
        return win_logloss_rows(race_ids, race_softmax(race_ids, scores, t), is_win)

    # log(T) の上で探す(T の大小に対して対称にするため)
    a, b = np.log(bounds[0]), np.log(bounds[1])
    gr = (np.sqrt(5) - 1) / 2
    c, d = b - gr * (b - a), a + gr * (b - a)
    fc, fd = loss(np.exp(c)), loss(np.exp(d))
    while b - a > tol:
        if fc < fd:
            b, d, fd = d, c, fc
            c = b - gr * (b - a)
            fc = loss(np.exp(c))
        else:
            a, c, fc = c, d, fd
            d = a + gr * (b - a)
            fd = loss(np.exp(d))
    t = float(np.exp((a + b) / 2))
    return t, loss(t)
